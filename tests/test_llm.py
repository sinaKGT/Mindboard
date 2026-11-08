"""The local model's plumbing: what gets downloaded, how it is checked, and how
llama-server is started, guarded and stopped.

Nothing here downloads anything or loads a model. Downloads go through a fake
transport; llama-server is played by tests/fake_llama_server.py, which takes the
same flags and answers the same endpoints.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from mindboard import llm

FAKE = Path(__file__).with_name("fake_llama_server.py")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ------------------------------------------------------------------ pins --

def model_pins():
    return [p for t in llm.TIERS for p in (t.model, t.vision)] + [llm.EMBEDDER, llm.RERANKER]


def all_pins():
    return [p for pins in llm.RUNTIMES.values() for p in pins] + model_pins()


def test_every_file_is_pinned_exactly():
    for pin in all_pins():
        assert HEX64.match(pin.sha256), pin.name
        assert pin.size > 0, pin.name
        assert pin.url.startswith("https://"), pin.name
        assert pin.url.endswith("/" + pin.name), pin.name


def test_nothing_floats_to_latest():
    """An update is a change to llm.py, never whatever is newest that day."""
    for pins in llm.RUNTIMES.values():
        for pin in pins:
            assert pin.url.startswith(
                f"https://github.com/ggml-org/llama.cpp/releases/download/{llm.LLAMA_BUILD}/")
    for pin in model_pins():
        assert re.search(r"/resolve/[0-9a-f]{40}/", pin.url), f"{pin.url}: a commit, not a branch"
        assert "/main/" not in pin.url and "latest" not in pin.url


# ----------------------------------------------------------------- sizes --

def test_sizes_run_largest_first_and_end_with_one_for_anyone():
    needs = [t.needs_gib for t in llm.TIERS]
    assert needs == sorted(needs, reverse=True)
    assert llm.TIERS[-1].needs_gib == 0, "some model must run on any computer"
    for tier in llm.TIERS[:-1]:
        assert tier.run_gib < tier.needs_gib + 0.5, f"{tier.key} would not fit the card it is for"


def test_files_that_share_a_folder_are_the_same_files():
    """Every repository names its vision part mmproj-F16.gguf; two sizes may only
    share a folder when that file is truly the same one."""
    by_folder: dict = {}
    for tier in llm.TIERS:
        for pin in (tier.model, tier.vision):
            seen = by_folder.setdefault((tier.folder, pin.name), pin.sha256)
            assert seen == pin.sha256, f"{tier.folder}/{pin.name} would hold two different files"


@pytest.mark.parametrize("memory, expected", [
    (16303 / 1024, "full"),         # a "16 GB" card, as its driver reports it
    (24 * 1.0, "full"),
    (12282 / 1024, "full"),         # 12 GB
    (8188 / 1024, "compact"),       # an 8 GB laptop card
    (6.0, "light"),
    (None, "light"),                # no graphics card at all
    (32 * 2 / 3, "full"),           # a 32 GB Mac's share
    (16 * 2 / 3, "compact"),        # a 16 GB Mac
    (8 * 2 / 3, "light"),           # an 8 GB Mac
])
def test_picks_the_largest_model_that_fits_whole(memory, expected):
    accel = llm.Accelerator("card", memory) if memory is not None else None
    assert llm.choose_tier(accel).key == expected


def test_the_model_is_qwen_9b_never_the_27b():
    """Decided in use: the 27B was far too slow to think with. It is not offered."""
    assert llm.TIERS[0].model.name.startswith("Qwen3.5-9B")
    assert not any("27B" in t.model.name for t in llm.TIERS)


def test_a_size_can_be_asked_for_by_name():
    assert llm.choose_tier(None, "full").key == "full"
    with pytest.raises(llm.SetupError, match="no model size"):
        llm.choose_tier(None, "huge")


def test_reads_the_biggest_nvidia_card(monkeypatch):
    out = "NVIDIA GeForce RTX 3060, 12288\nNVIDIA GeForce RTX 4090, 24564\n"
    monkeypatch.setattr(llm.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out))
    accel = llm.detect_accelerator("windows", "cuda")
    assert accel.name == "NVIDIA GeForce RTX 4090"
    assert round(accel.memory_gib) == 24


def test_built_in_graphics_do_not_count_as_a_card(monkeypatch):
    monkeypatch.setattr(llm, "_windows_cards",
                        lambda: [llm.Accelerator("Intel(R) Graphics", 0.5)])
    assert llm.detect_accelerator("windows", "vulkan") is None


def _nvidia_says(monkeypatch, free_mib: int):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=f"{free_mib}\n"))


def test_says_how_much_memory_is_free_and_names_nothing(tmp_path, monkeypatch):
    """Mindboard runs its own llama.cpp and depends on no other program, so the
    warning is about memory and nothing else."""
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    _nvidia_says(monkeypatch, 3687)
    message = llm.memory_shortfall(inst)
    assert message and "3.6 GB" in message and "Mindboard's model needs" in message
    assert "program" not in message.lower() and " is using" not in message


def test_quiet_when_there_is_room(tmp_path, monkeypatch):
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    _nvidia_says(monkeypatch, 15600)
    assert llm.memory_shortfall(inst) is None


@pytest.mark.parametrize("accel", [llm.Accelerator("Some GPU", 8.0), None])
def test_the_setup_screen_says_what_and_why_before_downloading(tmp_path, monkeypatch, accel):
    """Every size, with and without a card: the screen a first-time person sees
    must render, name the size and its download, and offer the others."""
    import io

    from rich.console import Console

    from mindboard import llm_setup

    for tier in llm.TIERS:
        plan = llm.Plan(tmp_path, "windows", "cuda", accel, tier, [tier.model, tier.vision])
        monkeypatch.setattr(llm, "plan", lambda **kw: plan)
        out = io.StringIO()
        llm_setup.describe(Console(file=out, width=200, highlight=False))
        text = out.getvalue()
        assert tier.label in text and tier.model.name in text
        assert all(t.key in text for t in llm.TIERS if t is not tier), "other sizes offered"
        assert "--model" in text


# --------------------------------------------------------------- backend --

def test_backend_follows_the_hardware(monkeypatch):
    monkeypatch.setattr(llm.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(llm, "_has_nvidia", lambda: True)
    assert llm.choose_backend("windows") == "cuda"
    monkeypatch.setattr(llm, "_has_nvidia", lambda: False)
    monkeypatch.setattr(llm, "_has_vulkan", lambda plat: True)
    assert llm.choose_backend("windows") == "vulkan"
    monkeypatch.setattr(llm, "_has_vulkan", lambda plat: False)
    assert llm.choose_backend("linux") == "cpu"
    monkeypatch.setattr(llm.platform, "machine", lambda: "arm64")
    assert llm.choose_backend("macos") == "metal"


def test_asking_for_a_build_that_does_not_exist_says_so():
    with pytest.raises(llm.SetupError):
        llm.choose_backend("linux", "cuda")


# ----------------------------------------------------------- downloading --

BLOB = os.urandom(3 * llm.CHUNK + 1234)


def pin_for(data: bytes, name: str = "blob.bin") -> llm.Pinned:
    return llm.Pinned(name, f"https://example.test/{name}", len(data),
                      hashlib.sha256(data).hexdigest())


def serving(data: bytes, seen: list, honour_range: bool = True, cut: int = 0):
    """A transport that serves `data`, records each request, and can stop short."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rng = request.headers.get("range")
        if rng and honour_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data[:len(data) - cut] if cut else data)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_downloads_checks_and_marks(tmp_path):
    seen = []
    pin = pin_for(BLOB)
    got = llm.download(pin, tmp_path, client=serving(BLOB, seen))
    assert got.read_bytes() == BLOB
    assert llm.is_verified(got, pin)
    assert not (tmp_path / "blob.bin.part").exists()
    # already here and checked: no second request
    llm.download(pin, tmp_path, client=serving(BLOB, seen))
    assert len(seen) == 1


def test_carries_on_where_it_stopped(tmp_path):
    pin = pin_for(BLOB)
    half = len(BLOB) // 2
    (tmp_path / "blob.bin.part").write_bytes(BLOB[:half])
    seen = []
    got = llm.download(pin, tmp_path, client=serving(BLOB, seen))
    assert seen[0].headers["range"] == f"bytes={half}-"
    assert got.read_bytes() == BLOB


def test_starts_again_when_the_server_ignores_the_range(tmp_path):
    pin = pin_for(BLOB)
    (tmp_path / "blob.bin.part").write_bytes(BLOB[:1000])
    got = llm.download(pin, tmp_path, client=serving(BLOB, [], honour_range=False))
    assert got.read_bytes() == BLOB


def test_a_wrong_file_is_deleted_not_kept(tmp_path):
    pin = pin_for(BLOB)
    wrong = bytearray(BLOB)
    wrong[100] ^= 0xFF
    with pytest.raises(llm.SetupError, match="checksum"):
        llm.download(pin, tmp_path, client=serving(bytes(wrong), []))
    assert not any(tmp_path.iterdir())


def test_a_cut_off_download_is_kept_to_resume(tmp_path):
    pin = pin_for(BLOB)
    with pytest.raises(llm.SetupError, match="incomplete"):
        llm.download(pin, tmp_path, client=serving(BLOB, [], cut=5000))
    assert (tmp_path / "blob.bin.part").stat().st_size == len(BLOB) - 5000


# ------------------------------------------------------------ installing --

def make_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_unpacks_the_server_beside_its_cuda_runtime(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    main = make_zip(src / "llama.zip", {f"build/bin/{llm.SERVER_NAME}": b"exe",
                                        "build/bin/ggml-cuda.dll": b"dll"})
    cudart = make_zip(src / "cudart.zip", {"cudart64_13.dll": b"rt"})
    pins = [pin_for(main.read_bytes(), "llama.zip"), pin_for(cudart.read_bytes(), "cudart.zip")]
    monkeypatch.setitem(llm.RUNTIMES, ("windows", "cuda"), pins)

    def local_download(pin, folder, progress=None, client=None):
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / pin.name
        out.write_bytes((src / pin.name).read_bytes())
        return out
    monkeypatch.setattr(llm, "download", local_download)

    home = tmp_path / "home"
    target = llm.install_runtime(home, "windows", "cuda")

    bin_dir = target / json.loads((target / ".complete").read_text())["bin"]
    assert (bin_dir / llm.SERVER_NAME).exists()
    assert (bin_dir / "cudart64_13.dll").exists(), "the CUDA runtime must sit beside the server"
    assert not list((home / "downloads").glob("*.zip")), "archives go once unpacked"


def test_an_archive_cannot_write_outside_its_folder(tmp_path):
    bad = make_zip(tmp_path / "bad.zip", {"../escaped.txt": b"x"})
    with pytest.raises(llm.SetupError):
        llm._extract(bad, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_refuses_to_fill_the_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 3 * 1024 ** 3})())
    monkeypatch.setattr(llm, "choose_backend", lambda plat, want: "cpu")
    with pytest.raises(llm.SetupError, match="GB free"):
        llm.install(tmp_path)


def fake_install(tmp_path: Path, tier: str = "light", backend: str = "cpu") -> llm.Install:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / llm.SERVER_NAME).write_bytes(b"")
    return llm.Install(tmp_path, llm.this_platform(), backend, llm.TIER_BY_KEY[tier], bin_dir,
                       tmp_path / "model.gguf", tmp_path / "mmproj.gguf")


def test_not_set_up_until_every_piece_is_there(tmp_path):
    tier = llm.TIER_BY_KEY["compact"]
    assert llm.find_install(tmp_path) is None
    folder = llm.runtime_dir(tmp_path, llm.this_platform(), "cpu")
    folder.mkdir(parents=True)
    (folder / llm.SERVER_NAME).write_bytes(b"")
    (folder / ".complete").write_text(json.dumps({"bin": "."}))
    (tmp_path / "installed.json").write_text(json.dumps({"backend": "cpu", "tier": "compact"}))
    assert llm.find_install(tmp_path) is None, "no model yet"
    for pin in (tier.model, tier.vision):
        f = llm.model_path(tmp_path, tier, pin)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        if sys.platform == "win32":
            # on NTFS a truncated file takes its whole size on disk unless it is
            # marked sparse first - without this, each run left 6 GB in the temp folder
            subprocess.run(["fsutil", "sparse", "setflag", str(f)], capture_output=True, check=False)
        with f.open("r+b") as fh:
            # not truncate(): on Windows it extends by writing zeros. Writing only
            # the last byte leaves the gap unallocated - the right size, no 6 GB written
            fh.seek(pin.size - 1)
            fh.write(b"\0")
        llm._verified_mark(f).write_text(pin.sha256)
    found = llm.find_install(tmp_path)
    assert found is not None and found.server.exists() and found.tier.key == "compact"


# --------------------------------------------------------------- running --

@pytest.fixture()
def fake_server(monkeypatch):
    """Run the stand-in instead of llama-server, with the real arguments."""
    extra: list[str] = []

    def args(inst, port, key, ctx, vision_on_gpu=False):
        real = llm.server_args.__wrapped__(inst, port, key, ctx, vision_on_gpu)
        return [sys.executable, str(FAKE), *real[1:], *extra]
    args.__wrapped__ = llm.server_args
    monkeypatch.setattr(llm, "server_args", args)
    return extra


def test_serves_only_this_machine_and_only_with_its_key(tmp_path, fake_server):
    inst = fake_install(tmp_path)
    args = llm.server_args.__wrapped__(inst, 4321, "k3y", 32768)
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--api-key") + 1] == "k3y"
    assert "--no-webui" in args and "--no-slots" in args
    assert args[args.index("--mmproj") + 1] == str(inst.vision)
    assert "--n-gpu-layers" not in args, "--fit decides layers; a fixed number would defeat it"

    model = llm.LocalModel(inst, start_timeout=20)
    try:
        model.start()
        assert model.running()
        assert httpx.post(model.base + "/v1/chat/completions", json={"messages": []},
                          timeout=5).status_code == 401
        out = model.chat([{"role": "user", "content": "hello"}])
        assert out["text"].startswith("A mind map")
        assert out["tokens_per_second"] == 42.0
        assert llm.layers_on_gpu(model.log_path.read_text()) == (65, 65)
    finally:
        model.stop()
    assert not model.running()


def test_a_key_is_never_mistaken_for_a_flag(tmp_path, monkeypatch):
    """A key starting with "-" would be read as the next flag; this broke one
    test run in four before keys became hex."""
    import secrets

    keys = []
    monkeypatch.setattr(llm.subprocess, "Popen",
                        lambda args, **kw: keys.append(args[args.index("--api-key") + 1])
                        or (_ for _ in ()).throw(OSError("stop here")))
    model = llm.LocalModel(fake_install(tmp_path), start_timeout=1)
    for _ in range(200):
        with pytest.raises(OSError):
            model.start()
    assert keys and not any(k.startswith("-") for k in keys)
    assert all(re.fullmatch(r"[0-9a-f]{48}", k) for k in keys)


def test_a_new_key_every_start(tmp_path, fake_server):
    model = llm.LocalModel(fake_install(tmp_path), start_timeout=20)
    try:
        model.start()
        first = model.key
        model.stop()
        model.start()
        assert model.key != first
    finally:
        model.stop()


def test_says_what_went_wrong_when_it_cannot_load(tmp_path, fake_server):
    fake_server.append("--fail")
    model = llm.LocalModel(fake_install(tmp_path), start_timeout=20)
    with pytest.raises(llm.ModelError, match="out of memory"):
        model.start()
    assert not model.running()


def test_gives_the_memory_back_when_idle(tmp_path, fake_server):
    model = llm.LocalModel(fake_install(tmp_path), idle_minutes=0.005, start_timeout=20)
    try:
        model.start()
        deadline = time.time() + 10
        while model.running() and time.time() < deadline:
            time.sleep(0.1)
        assert not model.running(), "an idle model should unload itself"
    finally:
        model.stop()


HELPER = """
import sys, time
from pathlib import Path
from mindboard import llm
home, fake = Path(sys.argv[1]), sys.argv[2]
real = llm.server_args
llm.server_args = lambda *a, **k: [sys.executable, fake, *real(*a, **k)[1:]]
bin_dir = home / "bin"; bin_dir.mkdir(parents=True, exist_ok=True)
inst = llm.Install(home, llm.this_platform(), "cpu", llm.TIERS[-1], bin_dir, home / "m", home / "v")
m = llm.LocalModel(inst, start_timeout=20)
m.start()
print(m.proc.pid, flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    import ctypes

    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, pid)                  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102   # still running
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="the job object is Windows")
def test_the_server_dies_with_mindboard_even_when_killed(tmp_path):
    """The trap: kill Mindboard outright and a model holding 15 GB of graphics
    memory would otherwise run on, owned by nobody.

    Run on the base interpreter, not the venv's: on Windows the venv's python.exe
    is a launcher that starts the real one as a second process, which would make
    "kill Mindboard" kill the wrong thing."""
    base = getattr(sys, "_base_executable", sys.executable)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(llm.__file__).parents[1]),
                                         sysconfig.get_paths()["purelib"]])
    helper = subprocess.Popen([base, "-c", HELPER, str(tmp_path), str(FAKE)],
                              stdout=subprocess.PIPE, text=True, env=env)
    try:
        server_pid = int(helper.stdout.readline())
        assert _alive(server_pid)
        helper.kill()                                   # no atexit, no cleanup: a crash
        helper.wait(timeout=10)
        deadline = time.time() + 10
        while _alive(server_pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not _alive(server_pid), "llama-server outlived the process that started it"
    finally:
        if helper.poll() is None:
            helper.kill()


def test_the_check_picture_has_the_word_in_it():
    from PIL import Image
    import io

    im = Image.open(io.BytesIO(llm.test_picture()))
    assert im.size == (720, 360)


@pytest.mark.parametrize("free_mib, expected", [(13078, True), (11000, False)])
def test_the_vision_part_goes_on_the_gpu_when_it_fits(tmp_path, monkeypatch, free_mib, expected):
    """On the GPU a picture-heavy board was read five times faster; when the
    card is short, the vision part stays off it rather than push the model off."""
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    _nvidia_says(monkeypatch, free_mib)
    assert llm.vision_fits(inst) is expected
    assert llm.vision_fits(fake_install(tmp_path, tier="full", backend="cpu")) is False


def test_the_start_decides_where_the_vision_part_runs(tmp_path, fake_server, monkeypatch):
    seen = []
    real = llm.server_args

    def spy(inst, port, key, ctx, vision_on_gpu=False):
        seen.append(vision_on_gpu)
        return real(inst, port, key, ctx, vision_on_gpu)
    spy.__wrapped__ = real.__wrapped__
    monkeypatch.setattr(llm, "server_args", spy)
    monkeypatch.setattr(llm, "vision_fits", lambda inst: True)
    model = llm.LocalModel(fake_install(tmp_path), start_timeout=20)
    try:
        model.start()
        assert seen == [True] and model.vision_placed == "GPU"
    finally:
        model.stop()


# --------------------------------------------------------- search models --

def test_the_search_models_are_part_of_the_install(tmp_path):
    names = {pin.name for pin in llm.needed(tmp_path, llm.this_platform(), "cpu",
                                            llm.TIER_BY_KEY["light"])}
    assert llm.EMBEDDER.name in names and llm.RERANKER.name in names


class _Running:
    """A model as make_room sees it: running, on the card, with a priority;
    stopping it gives back `gib` of graphics memory."""

    def __init__(self, card, priority, gib, log):
        self.card, self.priority, self.gib, self.log = card, priority, gib, log
        self.alive = True

    def running(self):
        return self.alive

    def on_card(self):
        return True

    def evict(self):
        self.alive = False
        self.card["free"] += self.gib
        self.log.append(self.priority)


@pytest.fixture()
def card(monkeypatch):
    """A graphics card whose free memory the tests set, and the models on it."""
    state = {"free": 0.0}
    monkeypatch.setattr(llm, "free_gpu_gib", lambda inst: state["free"])
    monkeypatch.setattr(llm, "_live", set())
    return state


def test_a_search_model_takes_the_card_while_there_is_room(tmp_path, card):
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    inst.embedder = tmp_path / "e.gguf"
    card["free"] = llm.HELPER_GIB + 0.1
    assert llm.helper_on_gpu(llm.EmbedModel(inst)) is True
    card["free"] = llm.HELPER_GIB - 0.1
    assert llm.helper_on_gpu(llm.EmbedModel(inst)) is False, "no room, none to make"
    cpu = fake_install(tmp_path, backend="cpu")
    cpu.embedder = inst.embedder
    assert llm.helper_on_gpu(llm.EmbedModel(cpu)) is False


def test_the_chat_model_takes_the_card_back_least_important_first(tmp_path, card):
    """The 9B writes every answer: when it loads and the search models fill the
    card, they give it back - the embedder first, the reranker only if that is
    not enough."""
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    stopped = []
    embedder, reranker = _Running(card, 2, 1.1, stopped), _Running(card, 1, 1.1, stopped)
    llm._live.update({embedder, reranker})
    card["free"] = llm.chat_gib(inst) - 0.5
    assert llm.make_room(llm.LocalModel(inst), llm.chat_gib(inst)) is True
    assert stopped == [2] and reranker.alive, "one was enough"
    embedder.alive, card["free"] = True, llm.chat_gib(inst) - 1.5
    stopped.clear()
    assert llm.make_room(llm.LocalModel(inst), llm.chat_gib(inst)) is True
    assert stopped == [2, 1]


def test_a_model_never_takes_the_card_from_one_that_matters_more(tmp_path, card):
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    inst.reranker = tmp_path / "r.gguf"
    stopped = []
    chat, embedder = _Running(card, 0, 10.0, stopped), _Running(card, 2, 1.1, stopped)
    llm._live.update({chat, embedder})
    card["free"] = 0.5
    assert llm.helper_on_gpu(llm.RerankModel(inst)) is True, "room made from the embedder"
    assert stopped == [2] and chat.alive
    card["free"] = 0.5
    assert llm.helper_on_gpu(llm.RerankModel(inst)) is False, "the chat model stays"
    assert chat.alive


def test_where_free_memory_cannot_be_read_llama_decides(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    inst.embedder = tmp_path / "e.gguf"
    assert llm.helper_on_gpu(llm.EmbedModel(inst)) is True
    assert llm.make_room(llm.LocalModel(inst), 99.0) is True


@pytest.fixture()
def fake_embed_server(monkeypatch):
    def args(inst, port, key, ctx, on_gpu=True):
        real = args.__wrapped__(inst, port, key, ctx, on_gpu)
        return [sys.executable, str(FAKE), *real[1:]]
    args.__wrapped__ = llm.embed_server_args
    monkeypatch.setattr(llm, "embed_server_args", args)


def test_taking_the_card_back_never_cuts_a_request_off(tmp_path, fake_embed_server, monkeypatch):
    """Stopped to make room, a search model first finishes what it is doing;
    a request that comes meanwhile waits, then starts it again - placed
    afresh, here on the processor."""
    import threading

    inst = fake_install(tmp_path)
    inst.embedder = tmp_path / "e.gguf"
    places = iter([True, False])
    monkeypatch.setattr(llm, "helper_on_gpu", lambda model: next(places))
    model = llm.EmbedModel(inst, start_timeout=20)
    try:
        model.embed(["warm"])
        assert model.on_gpu is True
        first_proc = model.proc
        got = {}
        slow = threading.Thread(target=lambda: got.setdefault("slow", model.embed(["SLOWLY"])))
        slow.start()
        time.sleep(0.3)                                  # the slow request is in flight
        evicting = threading.Thread(target=model.evict)
        evicting.start()
        time.sleep(0.1)
        late = threading.Thread(target=lambda: got.setdefault("late", model.embed(["late"])))
        late.start()
        for t in (slow, evicting, late):
            t.join(timeout=20)
        assert len(got["slow"]) == 1, "the request in flight finished"
        assert len(got["late"]) == 1, "the one that came meanwhile was answered"
        assert model.proc is not first_proc and model.on_gpu is False, "restarted, placed afresh"
    finally:
        model.stop()


def test_a_search_model_kept_off_the_card_runs_on_the_processor(tmp_path):
    inst = fake_install(tmp_path)
    for make in (llm.embed_server_args, llm.rerank_server_args):
        assert "--device" not in make(inst, 1, "k", 2048)
        off = make(inst, 1, "k", 2048, on_gpu=False)
        assert off[off.index("--device") + 1] == "none"
    assert "--rerank" in llm.rerank_server_args(inst, 1, "k", 2048)


@pytest.fixture()
def fake_rerank_server(monkeypatch):
    def args(inst, port, key, ctx, on_gpu=True):
        real = args.__wrapped__(inst, port, key, ctx, on_gpu)
        return [sys.executable, str(FAKE), *real[1:]]
    args.__wrapped__ = llm.rerank_server_args
    monkeypatch.setattr(llm, "rerank_server_args", args)


def test_reranks_in_the_order_it_was_given(tmp_path, fake_rerank_server, monkeypatch):
    """llama-server answers best first; the scores come back in the order the
    cards were sent, so each stays with its card."""
    inst = fake_install(tmp_path)
    inst.reranker = tmp_path / "rerank.gguf"
    monkeypatch.setattr(llm, "helper_on_gpu", lambda model: True)
    model = llm.RerankModel(inst, start_timeout=20)
    try:
        scores = model.rerank("chunking keeps context",
                              ["weather notes", "chunking keeps context whole", "chunking"])
        assert scores[0] == 0.0 and scores[1] == 1.0 and 0 < scores[2] < 1
        assert model.rerank("q", []) == []
        assert model.capacity() == llm.RerankModel.POOL_GPU
    finally:
        model.stop()


def test_on_the_processor_the_reranker_reads_fewer(tmp_path, fake_rerank_server, monkeypatch):
    inst = fake_install(tmp_path)
    inst.reranker = tmp_path / "rerank.gguf"
    monkeypatch.setattr(llm, "helper_on_gpu", lambda model: False)
    model = llm.RerankModel(inst, start_timeout=20)
    try:
        assert model.capacity() == llm.RerankModel.POOL_CPU < llm.RerankModel.POOL_GPU
    finally:
        model.stop()


def test_without_the_reranker_installed_it_says_how_to_get_it(tmp_path):
    with pytest.raises(llm.ModelError, match="mindboard ai install"):
        llm.RerankModel(fake_install(tmp_path))


def test_a_loaded_model_does_not_warn_about_its_own_memory(tmp_path, fake_server, monkeypatch):
    """Loaded, the model holds its 10 GB, and the card shows that much less
    free. Read then, every question after the first warned of a shortage that
    was the model itself. The warning is decided as it loads."""
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    monkeypatch.setattr(llm, "vision_fits", lambda inst: True)
    model = llm.LocalModel(inst, start_timeout=20)
    _nvidia_says(monkeypatch, 15600)
    assert model.memory_note() is None, "before loading: room, so no warning"
    try:
        model.start()
        _nvidia_says(monkeypatch, 2300)                 # the model's own share, now taken
        assert model.memory_note() is None
    finally:
        model.stop()


def test_a_model_that_loaded_short_of_memory_keeps_saying_so(tmp_path, fake_server, monkeypatch):
    inst = fake_install(tmp_path, tier="full", backend="cuda")
    monkeypatch.setattr(llm, "vision_fits", lambda inst: True)
    model = llm.LocalModel(inst, start_timeout=20)
    _nvidia_says(monkeypatch, 3000)
    try:
        model.start()
        _nvidia_says(monkeypatch, 15600)
        note = model.memory_note()
        assert note and "2.9 GB is free" in note, "it answers slowly while it stays loaded"
    finally:
        model.stop()
