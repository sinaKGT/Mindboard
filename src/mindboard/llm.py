"""The local model behind Ask your board: llama.cpp's server running a Qwen
model sized for this computer (DESIGN.md 23).

Nothing here touches the network unless the person chooses to set it up -
`mindboard ai install`, or the menu - and every file is pinned: exact address,
size and SHA-256. What runs is what was chosen; updating is a change to this
file, never "latest". Once installed, asking a board talks to nothing but a
process on 127.0.0.1 that Mindboard itself started.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__

# ------------------------------------------------------------------ pins --

LLAMA_BUILD = "b12102"
_GH = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/"

@dataclass(frozen=True)
class Pinned:
    name: str
    url: str
    size: int
    sha256: str


def _gh(name: str, size: int, sha: str) -> Pinned:
    return Pinned(name, _GH + name, size, sha)


def _hf(repo: str, commit: str, name: str, size: int, sha: str) -> Pinned:
    """A file from a model repository, at one fixed commit - never `main`."""
    return Pinned(name, f"https://huggingface.co/{repo}/resolve/{commit}/{name}", size, sha)


# One llama.cpp build per kind of computer. The CUDA build carries its own CUDA
# runtime in the second archive - nothing is installed system-wide.
RUNTIMES: dict[tuple[str, str], list[Pinned]] = {
    ("windows", "cuda"): [
        _gh("llama-b12102-bin-win-cuda-13.4-x64.zip", 169175458,
            "5d86a49bf4e3487735c396f8bc95c8b53b011c2f3e72c41e05965474c8b4a098"),
        _gh("cudart-llama-bin-win-cuda-13.4-x64.zip", 423535356,
            "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"),
    ],
    ("windows", "vulkan"): [
        _gh("llama-b12102-bin-win-vulkan-x64.zip", 37660949,
            "b322f7d6ce8b2a31cf16980aa38986a6a27da2e55e406e770f0221621c38de08"),
    ],
    ("windows", "cpu"): [
        _gh("llama-b12102-bin-win-cpu-x64.zip", 24707363,
            "87d8c812e8a68a35de55c2e7caab280bf96199b79395045d7a67de4b1ad802a9"),
    ],
    ("macos", "metal"): [
        _gh("llama-b12102-bin-macos-arm64.tar.gz", 16651858,
            "461c5bee70ed4e45e4ad4c68662a76ca2bca801b89f48b73cfd346250fd82fff"),
    ],
    ("linux", "vulkan"): [
        _gh("llama-b12102-bin-ubuntu-vulkan-x64.tar.gz", 35865501,
            "c00cb79495dfdcecf153774cbe83dd82bab68fd3c8d9042623bc9a504bfb46da"),
    ],
    ("linux", "cpu"): [
        _gh("llama-b12102-bin-ubuntu-x64.tar.gz", 23057584,
            "5ad9ec7bce33a2176c2f7cd5b522a5ba8a50100863b37aab683b992721ffea16"),
    ],
}

@dataclass(frozen=True)
class Tier:
    """One model size. Mindboard is used on all sorts of computers, so it picks
    the largest model that runs *whole* in the machine's graphics memory: a
    model that spills into system RAM reads those layers at RAM speed, several
    times slower, and a smaller model that fits beats a larger one that does not.

    All four are the same family - one architecture, one chat template, all
    able to read pictures - so everything built on top behaves the same on each.
    """
    key: str
    label: str
    needs_gib: float     # graphics memory that holds it whole, with room to think
    hint: str            # in words, for the person choosing
    folder: str          # each repository calls its vision part mmproj-F16.gguf
    model: Pinned
    vision: Pinned

    @property
    def download(self) -> int:
        return self.model.size + self.vision.size

    @property
    def run_gib(self) -> float:
        """Graphics memory it takes while loaded: weights, its memory of the
        conversation (32k tokens, q8_0) and working space."""
        return self.model.size / 2 ** 30 + 1.5


_Q38_27B = ("unsloth/Qwen3.8-27B-GGUF", "4ca720788d1e01f1bff70c033e0d0028fd02e502")
_Q35_9B = ("unsloth/Qwen3.5-9B-GGUF", "3885219b6810b007914f3a7950a8d1b469d598a5")
_Q35_4B = ("unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523")

# Largest first. Thresholds sit just under each class of card as the driver
# reports it - a "16 GB" card shows about 15.9 GiB, an "8 GB" laptop card 7.9.
TIERS: list[Tier] = [
    Tier("large", "Qwen3.8 27B", 15.5, "needs a 16 GB graphics card", "qwen3.8-27b",
         _hf(*_Q38_27B, "Qwen3.8-27B-UD-IQ4_XS.gguf", 14252845984,
             "40fac4050e940397dbf13087afd50f4734a11805bf9d65ef8ddd7483470e6199"),
         _hf(*_Q38_27B, "mmproj-F16.gguf", 927607488,
             "cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e")),
    Tier("medium", "Qwen3.5 9B, full quality", 11.5, "needs a 12 GB graphics card",
         "qwen3.5-9b",
         _hf(*_Q35_9B, "Qwen3.5-9B-Q8_0.gguf", 9527502048,
             "809626574d0cb43d4becfa56169980da2bb448f2299270f7be443cb89d0a6ae4"),
         _hf(*_Q35_9B, "mmproj-F16.gguf", 918166080,
             "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f")),
    Tier("small", "Qwen3.5 9B, compact", 7.5, "needs an 8 GB graphics card", "qwen3.5-9b",
         _hf(*_Q35_9B, "Qwen3.5-9B-Q4_K_M.gguf", 5680522464,
             "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"),
         _hf(*_Q35_9B, "mmproj-F16.gguf", 918166080,
             "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f")),
    Tier("light", "Qwen3.5 4B", 0.0, "runs on any computer, graphics card or not",
         "qwen3.5-4b",
         _hf(*_Q35_4B, "Qwen3.5-4B-Q4_K_M.gguf", 2740937888,
             "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"),
         _hf(*_Q35_4B, "mmproj-F16.gguf", 672423616,
             "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864")),
]
TIER_BY_KEY = {t.key: t for t in TIERS}

CHUNK = 1 << 20
SPARE = 2 * 1024 ** 3        # never fill a disk to the last byte


class SetupError(RuntimeError):
    """Something the person can act on, already phrased for them."""


# ----------------------------------------------------------------- where --

def llm_home(data_root: Optional[Path] = None) -> Path:
    """Where the runtime and the model live.

    Beside the install, not among the boards: it is part of the program, like
    `.venv`, so backups never carry 15 GB of model. A source checkout keeps it
    in `.llm/` (gitignored); an installed package keeps it in the data folder.
    `MINDBOARD_LLM_DIR` overrides both.
    """
    env = os.environ.get("MINDBOARD_LLM_DIR")
    if env:
        return Path(env).expanduser().resolve()
    from .runtime import source_root

    root = source_root()
    if root is not None:
        return root / ".llm"
    if data_root is None:
        from .paths import default_data_dir

        data_root = default_data_dir()
    return Path(data_root) / "llm"


def this_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-L"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _has_vulkan(plat: str) -> bool:
    if plat == "windows":
        return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" /
                "vulkan-1.dll").exists()
    import ctypes.util

    return ctypes.util.find_library("vulkan") is not None


def choose_backend(plat: Optional[str] = None, want: str = "auto") -> str:
    """CUDA for an NVIDIA card, Metal on a Mac, Vulkan for any other card, the
    processor when there is nothing else."""
    plat = plat or this_platform()
    if want != "auto":
        if (plat, want) not in RUNTIMES:
            raise SetupError(f"there is no {want} build of llama.cpp for {plat}")
        return want
    if plat == "macos":
        if platform.machine().lower() not in ("arm64", "aarch64"):
            raise SetupError("Ask your board needs a Mac with Apple silicon")
        return "metal"
    if platform.machine().lower() not in ("amd64", "x86_64", "x64"):
        raise SetupError("Ask your board needs a 64-bit Intel or AMD processor here")
    if (plat, "cuda") in RUNTIMES and _has_nvidia():
        return "cuda"
    if _has_vulkan(plat):
        return "vulkan"
    return "cpu"


# ------------------------------------------------------------ sizing --

@dataclass(frozen=True)
class Accelerator:
    name: str
    memory_gib: float     # what a model may use: a card's own memory, or a Mac's share


def _nvidia_cards() -> list[Accelerator]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    cards = []
    for line in out.strip().splitlines():
        name, _, mib = line.rpartition(",")
        try:
            cards.append(Accelerator(name.strip(), int(mib.strip()) / 1024))
        except ValueError:
            continue
    return cards


def _windows_cards() -> list[Accelerator]:
    """Any brand of card on Windows. The registry's qwMemorySize is the card's
    own memory; WMI's AdapterRAM cannot report more than 4 GB."""
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    cards = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    for i in range(64):
        try:
            sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
        except OSError:
            break
        try:
            raw, _ = winreg.QueryValueEx(sub, "HardwareInformation.qwMemorySize")
            name, _ = winreg.QueryValueEx(sub, "DriverDesc")
            size = raw if isinstance(raw, int) else int.from_bytes(raw[:8], "little")
            cards.append(Accelerator(str(name), size / 2 ** 30))
        except OSError:
            continue
    return cards


def _linux_cards() -> list[Accelerator]:
    cards = []
    for f in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            cards.append(Accelerator(f.parent.name, int(f.read_text().strip()) / 2 ** 30))
        except (OSError, ValueError):
            continue
    return cards


def _mac_share() -> list[Accelerator]:
    """A Mac's graphics share its memory; macOS lets them use about two thirds."""
    try:
        total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                   text=True, timeout=10).stdout.strip())
    except Exception:
        return []
    return [Accelerator("Apple silicon (shared memory)", total / 2 ** 30 * 2 / 3)]


def detect_accelerator(plat: str, backend: str) -> Optional[Accelerator]:
    """The graphics memory the model can run in, or None for the processor alone.
    A best guess before anything is downloaded; if it is off, llama.cpp's --fit
    still places what fits, and the model only runs slower than it could."""
    if backend == "cpu":
        return None
    if backend == "cuda":
        cards = _nvidia_cards()
    elif backend == "metal":
        cards = _mac_share()
    elif plat == "windows":
        cards = _windows_cards()
    else:
        cards = _linux_cards()
    # under 2 GB is a processor's built-in graphics borrowing system memory
    cards = [c for c in cards if c.memory_gib >= 2]
    return max(cards, key=lambda c: c.memory_gib) if cards else None


def choose_tier(accel: Optional[Accelerator], want: str = "auto") -> Tier:
    """The largest model that fits whole - or the one asked for by name."""
    if want != "auto":
        if want not in TIER_BY_KEY:
            raise SetupError(f"there is no model size called {want!r} - "
                             f"choose from {', '.join(TIER_BY_KEY)}")
        return TIER_BY_KEY[want]
    memory = accel.memory_gib if accel else 0.0
    return next(t for t in TIERS if memory >= t.needs_gib)


# ----------------------------------------------------------- downloading --

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(CHUNK * 8)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _verified_mark(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def is_verified(path: Path, pin: Pinned) -> bool:
    """Already here and already checked. Re-hashing 14 GB on every start would
    take a minute; the mark records that this exact file was checked once."""
    mark = _verified_mark(path)
    try:
        return (path.stat().st_size == pin.size
                and mark.read_text(encoding="utf-8").strip() == pin.sha256)
    except OSError:
        return False


Progress = Callable[[str, int, int], None]   # (file name, bytes done, bytes total)


def download(pin: Pinned, folder: Path, progress: Optional[Progress] = None,
             client=None) -> Path:
    """Fetch one pinned file into `folder`, resuming a partial one, and refuse it
    unless its size and SHA-256 are exactly what is pinned.

    The bytes go to `<name>.part` and are hashed as they arrive; only a file
    that checks out is renamed into place. A wrong file is deleted, not kept.
    """
    import httpx

    folder.mkdir(parents=True, exist_ok=True)
    final = folder / pin.name
    if is_verified(final, pin):
        return final
    part = final.with_name(final.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > pin.size:
        part.unlink()
        have = 0

    digest = hashlib.sha256()
    if have:
        with part.open("rb") as fh:                 # resuming: account for what is there
            while True:
                block = fh.read(CHUNK * 8)
                if not block:
                    break
                digest.update(block)

    own = client is None
    client = client or httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(60.0, read=120.0),
        headers={"User-Agent": f"mindboard/{__version__}"})
    try:
        if have < pin.size:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with client.stream("GET", pin.url, headers=headers) as res:
                if have and res.status_code == 200:
                    # the server ignored the range: start this file again
                    have = 0
                    digest = hashlib.sha256()
                elif res.status_code not in (200, 206):
                    raise SetupError(f"could not download {pin.name} "
                                     f"(the server answered {res.status_code})")
                with part.open("ab" if have else "wb") as out:
                    for chunk in res.iter_bytes(CHUNK):
                        out.write(chunk)
                        digest.update(chunk)
                        have += len(chunk)
                        if progress:
                            progress(pin.name, have, pin.size)
                        if have > pin.size:
                            break
    except httpx.HTTPError as exc:
        raise SetupError(f"the download of {pin.name} stopped ({exc.__class__.__name__}) - "
                         "run it again and it carries on where it left off") from exc
    finally:
        if own:
            client.close()

    size = part.stat().st_size
    if size != pin.size:
        if size > pin.size:
            part.unlink()
        raise SetupError(f"{pin.name} arrived incomplete - run it again to carry on")
    if digest.hexdigest() != pin.sha256:
        part.unlink()
        raise SetupError(f"{pin.name} did not match its checksum and was deleted. "
                         "Nothing was installed from it.")
    part.replace(final)
    _verified_mark(final).write_text(pin.sha256, encoding="utf-8")
    return final


# ------------------------------------------------------------ installing --

SERVER_NAME = "llama-server.exe" if this_platform() == "windows" else "llama-server"


def _extract(archive: Path, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():                # zipfile strips "..", but be sure
                target = (into / member).resolve()
                if into.resolve() not in target.parents and target != into.resolve():
                    raise SetupError(f"{archive.name} holds a file outside its folder")
            z.extractall(into)
        return
    with tarfile.open(archive) as t:
        try:
            t.extractall(into, filter="data")
        except TypeError:                              # Python without tar filters
            for m in t.getmembers():
                target = (into / m.name).resolve()
                if into.resolve() not in target.parents or m.issym() or m.islnk():
                    raise SetupError(f"{archive.name} holds a file outside its folder")
            t.extractall(into)


@dataclass
class Install:
    home: Path
    platform: str
    backend: str
    tier: Tier
    bin_dir: Path
    model: Path
    vision: Path

    @property
    def server(self) -> Path:
        return self.bin_dir / SERVER_NAME


def runtime_dir(home: Path, plat: str, backend: str) -> Path:
    return home / "llama.cpp" / f"{LLAMA_BUILD}-{plat}-{backend}"


def model_path(home: Path, tier: Tier, pin: Pinned) -> Path:
    return home / "models" / tier.folder / pin.name


def needed(home: Path, plat: str, backend: str, tier: Tier) -> list[Pinned]:
    """What would still have to be downloaded."""
    out: list[Pinned] = []
    if not (runtime_dir(home, plat, backend) / ".complete").exists():
        out += RUNTIMES[(plat, backend)]
    for pin in (tier.model, tier.vision):
        if not is_verified(model_path(home, tier, pin), pin):
            out.append(pin)
    return out


def install_runtime(home: Path, plat: str, backend: str,
                    progress: Optional[Progress] = None) -> Path:
    """Download and unpack one llama.cpp build. The archives are deleted once
    unpacked; the build folder only counts as installed when `.complete` is
    written, last."""
    target = runtime_dir(home, plat, backend)
    if (target / ".complete").exists():
        return target
    downloads = home / "downloads"
    staging = target.with_name(target.name + ".unpacking")
    shutil.rmtree(staging, ignore_errors=True)
    archives = [download(pin, downloads, progress) for pin in RUNTIMES[(plat, backend)]]
    for archive in archives:
        _extract(archive, staging)

    servers = list(staging.rglob(SERVER_NAME))
    if not servers:
        raise SetupError(f"{SERVER_NAME} was not in the llama.cpp download")
    bin_dir = servers[0].parent
    if bin_dir != staging:
        # the CUDA runtime arrives in a separate archive; it must sit beside the server
        for lib in staging.rglob("*.dll"):
            if lib.parent != bin_dir:
                shutil.move(str(lib), str(bin_dir / lib.name))
    if plat != "windows":
        for f in bin_dir.iterdir():
            if f.is_file() and not f.suffix:
                f.chmod(f.stat().st_mode | 0o111)

    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    (target / ".complete").write_text(
        json.dumps({"build": LLAMA_BUILD, "bin": str(bin_dir.relative_to(staging))}),
        encoding="utf-8")
    for archive in archives:
        archive.unlink(missing_ok=True)
        _verified_mark(archive).unlink(missing_ok=True)
    return target


def find_install(home: Optional[Path] = None) -> Optional[Install]:
    """The installed runtime and model, or None if Ask your board is not set up."""
    home = home or llm_home()
    plat = this_platform()
    try:
        record = json.loads((home / "installed.json").read_text(encoding="utf-8"))
        backend, tier = record["backend"], TIER_BY_KEY[record["tier"]]
    except (OSError, ValueError, KeyError):
        return None
    folder = runtime_dir(home, plat, backend)
    try:
        rel = json.loads((folder / ".complete").read_text(encoding="utf-8"))["bin"]
    except (OSError, ValueError, KeyError):
        return None
    model = model_path(home, tier, tier.model)
    vision = model_path(home, tier, tier.vision)
    if not (is_verified(model, tier.model) and is_verified(vision, tier.vision)):
        return None
    inst = Install(home, plat, backend, tier, folder / rel, model, vision)
    return inst if inst.server.exists() else None


@dataclass(frozen=True)
class Plan:
    """What setting up would do on this computer, worked out before anything
    is downloaded so the person can see it and say no."""
    home: Path
    platform: str
    backend: str
    accelerator: Optional[Accelerator]
    tier: Tier
    todo: list


def plan(home: Optional[Path] = None, backend: str = "auto", tier: str = "auto") -> Plan:
    home = home or llm_home()
    plat = this_platform()
    chosen = choose_backend(plat, backend)
    accel = detect_accelerator(plat, chosen)
    size = choose_tier(accel, tier)
    return Plan(home, plat, chosen, accel, size, needed(home, plat, chosen, size))


def install(home: Optional[Path] = None, backend: str = "auto", tier: str = "auto",
            progress: Optional[Progress] = None) -> Install:
    """Everything Ask your board needs, downloaded once and checked."""
    p = plan(home, backend, tier)
    p.home.mkdir(parents=True, exist_ok=True)
    room = shutil.disk_usage(p.home).free
    want = sum(pin.size for pin in p.todo)
    if want + SPARE > room:
        raise SetupError(f"this needs {want / 1e9:.1f} GB free on the drive holding {p.home} "
                         f"and there is {room / 1e9:.1f} GB")
    install_runtime(p.home, p.platform, p.backend, progress)
    for pin in (p.tier.model, p.tier.vision):
        download(pin, model_path(p.home, p.tier, pin).parent, progress)
    (p.home / "installed.json").write_text(json.dumps({
        "build": LLAMA_BUILD, "platform": p.platform, "backend": p.backend,
        "tier": p.tier.key, "model": p.tier.model.name, "model_sha256": p.tier.model.sha256,
        "vision_sha256": p.tier.vision.sha256,
        "detected": ({"name": p.accelerator.name, "memory_gib": round(p.accelerator.memory_gib, 1)}
                     if p.accelerator else None),
    }, indent=2), encoding="utf-8")
    found = find_install(p.home)
    if found is None:
        raise SetupError("the download finished but the install is incomplete - run it again")
    return found


def memory_shortfall(inst: Install) -> Optional[str]:
    """Say so when another program holds the graphics memory this model needs.

    The model still loads - llama.cpp's --fit puts what does not fit on the
    processor - but it answers several times slower, and without this the
    person would only see a slow Mindboard. Programs that run models of their
    own are named; NVIDIA only, as its driver is the one that can say.
    """
    if inst.backend != "cuda":
        return None
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        free = int(subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=10).stdout.split()[0])
        apps = subprocess.run([exe, "--query-compute-apps=process_name", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.splitlines()
    except Exception:
        return None
    free_gib = free / 1024
    if free_gib >= inst.tier.run_gib:
        return None
    known = [("ollama", "Ollama"), ("lm studio", "LM Studio"), ("lmstudio", "LM Studio"),
             ("koboldcpp", "KoboldCpp"), ("gpt4all", "GPT4All"), ("\\jan\\", "Jan")]
    ours = str(inst.bin_dir).lower()
    holders: list[str] = []
    for path in apps:
        low = path.strip().lower()
        if not low or low.startswith(ours):
            continue
        for needle, name in known:
            if needle in low and name not in holders:
                holders.append(name)
    who = (" and ".join(holders) + (" are" if len(holders) > 1 else " is") + " using the rest"
           if holders else "other programs are using the rest")
    return (f"only {free_gib:.1f} GB of graphics memory is free and this model wants "
            f"{inst.tier.run_gib:.1f} GB - {who}. Answers will be slower until it is freed.")


# ------------------------------------------------------------- running --
#
# llama-server is its own process, not a library inside Mindboard: a model that
# runs out of memory takes down that process, not the app and its boards.

_JOB = None   # Windows: the job object every llama-server is placed in


def _dies_with_us(proc: subprocess.Popen) -> None:
    """Make the server stop when Mindboard stops, however Mindboard stops.

    A model this size holds about 15 GB of graphics memory. Without this, a
    Mindboard that crashed, or a launcher window closed with the X, would leave
    llama-server running with all of it. On Windows the server joins a job
    object that the OS kills when this process's last handle to it closes -
    which happens even if Python is killed outright.
    """
    global _JOB
    if this_platform() != "windows":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    if _JOB is None:
        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000          # KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                                      ctypes.sizeof(info)):
            return                                              # 9: extended limits
        _JOB = job                                              # held until we exit
    handle = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
    if handle:
        k32.AssignProcessToJobObject(_JOB, handle)
        k32.CloseHandle(handle)


def _linux_parent_death() -> None:   # runs in the child, before exec
    import ctypes
    import signal

    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_args(inst: Install, port: int, key: str, ctx: int,
                vision_on_gpu: bool = False) -> list[str]:
    """How llama-server is started. Every flag is deliberate - see DESIGN.md 23."""
    return [
        str(inst.server),
        "--model", str(inst.model),
        "--mmproj", str(inst.vision),
        # The vision part on the processor: on the GPU its ~0.9 GB pushes layers
        # of the model itself off the card, which slows every answer; on the
        # processor it slows only the reading of a picture.
        "--mmproj-offload" if vision_on_gpu else "--no-mmproj-offload",
        "--host", "127.0.0.1", "--port", str(port),
        "--api-key", key,             # localhost is not a trust boundary
        "--no-webui",                 # llama-server's own chat page: not ours to offer
        "--no-slots",                 # nor its monitoring endpoint
        "--ctx-size", str(ctx),
        # Layers deliberately left unset: --fit places as many on the GPU as
        # fit beside this context, and only adjusts what is not set by hand.
        "--fit", "on",
        "--flash-attn", "on",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--jinja",                    # the model's own chat template, images included
        "--parallel", "1",            # one person, one question at a time
    ]


class ModelError(RuntimeError):
    """The local model could not start or answer - phrased for the person."""


class LocalModel:
    """One llama-server, started on first use, unloaded when idle, stopped with
    Mindboard. Thread-safe: the app's request threads share one instance."""

    def __init__(self, inst: Install, ctx: int = 32768, idle_minutes: float = 20.0,
                 start_timeout: float = 600.0, vision_on_gpu: bool = False):
        self.inst = inst
        self.ctx = ctx
        self.vision_on_gpu = vision_on_gpu
        self.idle = idle_minutes * 60
        self.start_timeout = start_timeout
        self.proc: Optional[subprocess.Popen] = None
        self.port = 0
        self.key = ""
        self.last_used = 0.0
        self.busy = 0
        self.load_seconds = 0.0
        self._lock = threading.RLock()
        self._watcher: Optional[threading.Thread] = None
        atexit.register(self.stop)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log_path(self) -> Path:
        return self.inst.home / "logs" / "llama-server.log"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _log_tail(self, lines: int = 12) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    def start(self) -> None:
        import httpx

        with self._lock:
            if self.running():
                return
            self.port = _free_port()
            self.key = secrets.token_urlsafe(24)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log = self.log_path.open("wb")
            kw: dict = {"stdout": log, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL, "cwd": str(self.inst.bin_dir)}
            if this_platform() == "windows":
                kw["creationflags"] = 0x08000000                # CREATE_NO_WINDOW
            elif this_platform() == "linux":
                kw["preexec_fn"] = _linux_parent_death
            began = time.time()
            try:
                self.proc = subprocess.Popen(
                    server_args(self.inst, self.port, self.key, self.ctx, self.vision_on_gpu),
                    **kw)
            finally:
                log.close()
            _dies_with_us(self.proc)

            # 503 while the model loads, 200 once it can answer
            while True:
                if self.proc.poll() is not None:
                    self.proc = None
                    raise ModelError("the local model stopped while loading:\n"
                                     + self._log_tail())
                try:
                    if httpx.get(self.base + "/health", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.time() - began > self.start_timeout:
                    self.stop()
                    raise ModelError("the local model took too long to load")
                time.sleep(0.5)
            self.load_seconds = time.time() - began
            self.last_used = time.time()
            if self._watcher is None or not self._watcher.is_alive():
                self._watcher = threading.Thread(target=self._watch, daemon=True,
                                                 name="mindboard-llm-idle")
                self._watcher.start()

    def stop(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _watch(self) -> None:
        """Give the graphics memory back after a while without questions."""
        while True:
            time.sleep(min(30.0, max(self.idle / 4, 0.05)))
            with self._lock:
                if not self.running():
                    return
                if self.busy == 0 and time.time() - self.last_used > self.idle:
                    self.stop()
                    return

    def chat(self, messages: list[dict], *, max_tokens: int = 1024, think: bool = False,
             temperature: float = 0.7, timeout: float = 600.0) -> dict:
        """One answer, whole. Returns {text, prompt_tokens, prompt_per_second,
        tokens, tokens_per_second}."""
        import httpx

        self.start()
        with self._lock:
            self.busy += 1
        try:
            res = httpx.post(
                self.base + "/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={
                    "messages": messages, "max_tokens": max_tokens,
                    "temperature": temperature, "top_p": 0.8, "top_k": 20,
                    "chat_template_kwargs": {"enable_thinking": think},
                },
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"the local model did not answer ({exc.__class__.__name__})") from exc
        finally:
            with self._lock:
                self.busy -= 1
                self.last_used = time.time()
        if res.status_code != 200:
            raise ModelError(f"the local model refused the question ({res.status_code}): "
                             f"{res.text[:300]}")
        body = res.json()
        timings = body.get("timings") or {}
        return {
            "text": body["choices"][0]["message"].get("content") or "",
            "prompt_tokens": timings.get("prompt_n"),
            "prompt_per_second": timings.get("prompt_per_second"),
            "tokens": timings.get("predicted_n"),
            "tokens_per_second": timings.get("predicted_per_second"),
        }


# --------------------------------------------------------------- check --

def layers_on_gpu(log: str) -> Optional[tuple[int, int]]:
    """(on the GPU, in the model), as llama.cpp reports it while loading."""
    import re

    found = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", log)
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def gpu_memory_used_mib() -> Optional[int]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


CHECK_WORD = "MINDBOARD"


def test_picture() -> bytes:
    """A picture with a word in it the model can only know by reading it."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (720, 360), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([40, 80, 240, 280], fill="#d62828")
    try:
        font = ImageFont.truetype("arialbd.ttf" if this_platform() == "windows"
                                  else "DejaVuSans-Bold.ttf", 72)
    except OSError:
        font = ImageFont.load_default(size=72)
    d.text((280, 140), CHECK_WORD, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def check(inst: Install, vision_on_gpu: bool = False) -> dict:
    """Load the model, ask it something, show it a picture, and report how it
    went. Stops the model afterwards: a check must not leave 15 GB behind."""
    import base64

    model = LocalModel(inst, vision_on_gpu=vision_on_gpu)
    before = gpu_memory_used_mib()
    try:
        model.start()
        loaded = gpu_memory_used_mib()
        log = model.log_path.read_text(encoding="utf-8", errors="replace")
        text = model.chat([{"role": "user", "content":
                            "In one sentence: what is a mind map for?"}], max_tokens=120)
        picture = base64.b64encode(test_picture()).decode("ascii")
        seen = model.chat([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{picture}"}},
            {"type": "text", "text": "What word is written in this picture, and what shape and "
                                     "colour is beside it? One short sentence."},
        ]}], max_tokens=80)
    finally:
        model.stop()
    return {
        "load_seconds": round(model.load_seconds, 1),
        "layers": layers_on_gpu(log),
        "gpu_mib": (loaded - before) if (loaded is not None and before is not None) else None,
        "answer": text["text"].strip(),
        "tokens_per_second": text["tokens_per_second"],
        "saw": seen["text"].strip(),
        "read_the_picture": CHECK_WORD.lower() in seen["text"].lower(),
        "picture_prompt_seconds": (seen["prompt_tokens"] / seen["prompt_per_second"]
                                   if seen.get("prompt_per_second") else None),
    }
