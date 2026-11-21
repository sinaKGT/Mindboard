/** Back up every board (DESIGN.md 49).
 *
 *  Every file a card points to goes into the backup - from its own board, or
 *  the same file kept by another board. A file found nowhere on this computer
 *  cannot go in, and a backup that quietly lacked it would only be noticed
 *  after a restore. So the boards are checked first, which is quick, and any
 *  such file is named, by board and card, before the download starts; the
 *  backup also lists them inside, in MISSING-FILES.txt.
 */
import { api } from './api'

export async function backUp(before?: () => void): Promise<void> {
  before?.()
  try {
    const check = await api.backupCheck()
    if (check.missing.length) {
      const n = check.missing.length
      const shown = check.missing.slice(0, 8).map((m) =>
        `• ${m.cards.slice(0, 2).join(', ') || 'a card'}  (board “${m.board_title}”)`)
      const more = n > 8 ? `\n…and ${n - 8} more - listed in MISSING-FILES.txt inside the backup.` : ''
      const ok = confirm(`${n} file${n === 1 ? '' : 's'} on your boards could not be found on this computer, `
        + `so the backup cannot include ${n === 1 ? 'it' : 'them'}:\n\n${shown.join('\n')}${more}\n\n`
        + 'Everything else, and the cards themselves, will be backed up. Download the backup anyway?')
      if (!ok) return
    }
  } catch {
    // the check is a courtesy; a backup is never refused because it failed
  }
  location.href = api.backupUrl()
}
