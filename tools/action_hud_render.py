"""Render the real combat HUD and stress its intervals without a game server."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Panda3D'))
runtimeDlls = os.add_dll_directory(str(ROOT / 'Panda3D' / 'bin')) if os.name == 'nt' else None
os.chdir(ROOT)

from panda3d.core import ClockObject, Filename, loadPrcFileData, loadPrcFile

loadPrcFileData('hud-preview', '\n'.join((
    'window-type offscreen', 'win-size 1280 720', 'audio-library-name null',
    'language english', 'model-path /', 'sync-video false', 'default-model-extension .bam',
)))
loadPrcFile(Filename('config/development.prc'))
loadPrcFileData('preview-assets', 'model-path ' + Filename.fromOsSpecific(str(ROOT / 'resources')).getFullpath())
from direct.showbase.ShowBase import ShowBase

app = ShowBase()
app.setBackgroundColor(0.08, 0.14, 0.2, 1)
app.localAvatar = SimpleNamespace(
    getDoId=lambda: 1, uniqueName=lambda name: 'preview-' + name,
    getHp=lambda: 23, getMaxHp=lambda: 40, getMoney=lambda: 125,
    getTrackAccess=lambda: [1, 1, 1, 2, 2, 1, 1],
    getEquippedTracks=lambda: [4, 5, 3],
)
app.cr = SimpleNamespace(doId2do={42: SimpleNamespace(
    currHP=35, maxHP=80, getName=lambda: 'The Big Cheese')})

from toontown.action.ui.ActionHUD import ActionHUD
from toontown.action.ui.CombatAudio import CombatAudio
from toontown.action import ActionGlobals
from toontown.action.cog import CogAttackRegistry
from toontown.action.cog.CogAttackFX import CogAttackFX, _makeSector
from panda3d.core import Point3

# Use real geometry and intervals to check mutated impact size, not GUI stubs.
sector = _makeSector(8.0, 45.0, (1, 0.7, 0.2, 1))
assert not sector.isEmpty()
sector.removeNode()
node = app.render.attachNewNode('preview-cog')
suit = SimpleNamespace(isEmpty=node.isEmpty, getPos=node.getPos, getDoId=lambda: 42)
fx = CogAttackFX(suit)
from toontown.battle.SuitBattleGlobals import SuitAttackType
for attack in SuitAttackType:
    rt = CogAttackRegistry.getRealtimeAttackById(int(attack))
    if rt and rt.shape == CogAttackRegistry.SHAPE_AOE:
        mutated = CogAttackRegistry.mutate(rt, 4)
        captured = []
        fx.pendingAttacks[rt.attackId] = (mutated, Point3(0, 0, 0))
        fx._pulseRing = lambda pos, radius, color: captured.append(radius)
        fx.playResolved(rt.attackId, 99, 0)
        assert captured == [mutated.splashRadius]
        break
else:
    raise AssertionError('No AoE attack exercised')
fx.cleanup()
node.removeNode()

for path, _, _ in CombatAudio.CUES.values():
    assert (ROOT / 'resources' / path).is_file(), path

clock = ClockObject.getGlobalClock()
clock.setMode(ClockObject.MNonRealTime)
clock.setDt(1 / 60)
hud = ActionHUD('Silly Street')
app.messenger.send('action-tier', [4])
app.messenger.send('action-pressure', [280, 2, 2])
app.messenger.send('action-objectives', [[
    SimpleNamespace(kind=ActionGlobals.OBJ_DEFEAT_DEPT, param=0, target=8, progress=3, complete=False),
    SimpleNamespace(kind=ActionGlobals.OBJ_HITS_WITH_TRACK, param=4, target=20, progress=12, complete=False),
]])
app.messenger.send('action-announce', [ActionGlobals.ANNOUNCE_KILL_CHAIN, 1, 4])
app.messenger.send('action-gag-hit', [42, 4, 1, 12])
app.messenger.send('action-slot-fired', [0, 2.0])
for i in range(12):
    hud.addToast('Combat feedback %d' % i, (1, 0.85, 0.4, 1))
for _ in range(35):
    app.taskMgr.step()
app.graphicsEngine.renderFrame()
output = ROOT / 'screenshots' / 'action-hud-preview.png'
output.parent.mkdir(exist_ok=True)
assert app.win.saveScreenshot(Filename.fromOsSpecific(str(output)))
for _ in range(180):
    app.taskMgr.step()
assert not hud.toasts and not hud.toastTracks, 'toast intervals must release nodes'
hud.destroy()
for _ in range(3):
    app.taskMgr.step()
assert not app.taskMgr.hasTaskNamed('actionHudUpdate')
app.destroy()
print('HUD render and interval cleanup passed: %s' % output)
