"""Offscreen render and lifecycle check for escape and game-over reports."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Panda3D'))
if os.name == 'nt':
    os.add_dll_directory(str(ROOT / 'Panda3D' / 'bin'))
os.chdir(ROOT)

from panda3d.core import Filename, loadPrcFileData, loadPrcFile
loadPrcFileData('end-screen-preview', '\n'.join((
    'window-type offscreen', 'win-size 1280 720', 'audio-library-name null',
    'language english', 'model-path /', 'default-model-extension .bam', 'sync-video false',
)))
loadPrcFile(Filename('config/development.prc'))
loadPrcFileData('preview-assets', 'model-path ' + Filename.fromOsSpecific(str(ROOT / 'resources')).getFullpath())
from direct.showbase.ShowBase import ShowBase

app = ShowBase()
from toontown.action.ui.RunEndPanel import RunEndPanel

summary = {'kills': 27, 'beans': 1425, 'objectives': 4, 'dodges': 19,
           'damage': 56, 'caches': 1, 'pressure': 735, 'seconds': 387}
closed = []
panel = RunEndPanel('Silly Street', summary, escaped=False, doneCallback=lambda: closed.append(True))
for _ in range(30):
    app.taskMgr.step()
app.graphicsEngine.renderFrame()
output = ROOT / 'screenshots' / 'action-game-over-preview.png'
output.parent.mkdir(exist_ok=True)
assert app.win.saveScreenshot(Filename.fromOsSpecific(str(output)))
assert panel._formatTime(387) == '6:27'
panel.close()
assert closed == [True] and panel.backdrop is None
panel.close()
assert closed == [True], 'a closed report cannot invoke its callback twice'
escaped = RunEndPanel('Silly Street', summary, escaped=True)
escaped.destroy()
app.destroy()
print('End screen render and lifecycle passed: %s' % output)
