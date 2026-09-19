from panda3d.core import *
from direct.distributed import DistributedObject
from direct.interval.IntervalGlobal import (Func, LerpColorScaleInterval, LerpScaleInterval,
                                             Parallel, Sequence, Wait)
from panda3d.toontown import DNASuitPoint

from . import SuitPlannerBase
from toontown.action import ActionGlobals
from toontown.action.objectives.ObjectiveGenerator import Objective
from toontown.battle import BattleProps, MovieUtil
from toontown.battle.BattleSounds import globalBattleSoundCache
from toontown.toonbase import ToontownGlobals

class DistributedSuitPlanner(DistributedObject.DistributedObject, SuitPlannerBase.SuitPlannerBase):

    def __init__(self, cr):
        DistributedObject.DistributedObject.__init__(self, cr)
        SuitPlannerBase.SuitPlannerBase.__init__(self)
        self.suitList = []
        self.buildingList = [0,
         0,
         0,
         0]
        self.pathViz = None
        # Real-time street run state (Toontown Apocalypse)
        self.actionTier = ActionGlobals.DEFAULT_TIER
        self.actionPressure = 0
        self.actionStage = ActionGlobals.PRESSURE_CALM
        self.actionObjectives = []
        self.actionTraps = {}
        self.actionTracks = []
        return

    def generate(self):
        self.notify.info('DistributedSuitPlanner %d: generating' % self.getDoId())
        DistributedObject.DistributedObject.generate(self)
        base.cr.currSuitPlanner = self

    def announceGenerate(self):
        DistributedObject.DistributedObject.announceGenerate(self)
        messenger.send('action-planner-ready', [self])

    def disable(self):
        self.notify.info('DistributedSuitPlanner %d: disabling' % self.getDoId())
        self.hidePaths()
        self._clearActionTraps()
        for track in self.actionTracks:
            track.finish()
        self.actionTracks = []
        DistributedObject.DistributedObject.disable(self)
        if base.cr.currSuitPlanner is self:
            base.cr.currSuitPlanner = None
        messenger.send('action-planner-gone', [self])
        return

    # ------------------------------------------------------------------
    # Real-time street runs (Toontown Apocalypse)
    # ------------------------------------------------------------------
    def d_requestStreetRun(self, tier):
        self.sendUpdate('requestStreetRun', [int(tier)])

    def d_leaveStreetRun(self):
        self.sendUpdate('leaveStreetRun', [])

    def d_requestActionTrap(self, level, pos):
        self.sendUpdate('requestActionTrap', [int(level), pos[0], pos[1], pos[2]])

    def d_requestActionToonUp(self, level):
        self.sendUpdate('requestActionToonUp', [int(level)])

    def setActionTier(self, tier):
        self.actionTier = tier
        messenger.send('action-tier', [tier])

    def getActionTier(self):
        return self.actionTier

    def setActionPressure(self, pressure, stage):
        oldStage = self.actionStage
        self.actionPressure = pressure
        self.actionStage = stage
        messenger.send('action-pressure', [pressure, stage, oldStage])

    def getActionPressure(self):
        return self.actionPressure, self.actionStage

    def setActionObjectives(self, objectives):
        self.actionObjectives = [Objective.fromWire(item) for item in objectives]
        messenger.send('action-objectives', [self.actionObjectives])

    def getActionObjectives(self):
        return self.actionObjectives

    def actionObjectiveComplete(self, index, beans, track, xp):
        messenger.send('action-objective-complete', [index, beans, track, xp])

    def actionAnnounce(self, kind, avId, value):
        messenger.send('action-announce', [kind, avId, value])

    def actionToonUp(self, avId, level, amount):
        toon = self.cr.doId2do.get(avId)
        gagDef = ActionGlobals.getGagDef(ActionGlobals.HEAL_TRACK, level)
        if toon is not None and not toon.isEmpty():
            sfx = globalBattleSoundCache.getSound(gagDef.sound) if gagDef else None
            if sfx:
                base.playSfx(sfx, node=toon)
            sparkle = Sequence(Func(toon.setColorScale, Vec4(0.75, 1.0, 0.75, 1.0)), Wait(0.35),
                               Func(toon.clearColorScale), name=self.uniqueName('actionToonUp'))
            self._addActionTrack(sparkle)
        messenger.send('action-toon-up', [avId, level, amount])

    def _addActionTrack(self, track):
        self.actionTracks = [item for item in self.actionTracks if not item.isStopped()]
        self.actionTracks.append(track)
        track.start()

    # -- traps -----------------------------------------------------------
    def actionTrapPlaced(self, trapId, avId, level, x, y, z):
        gagDef = ActionGlobals.getGagDef(ActionGlobals.TRAP_TRACK, level)
        if gagDef is None:
            return
        self._removeActionTrap(trapId)
        try:
            prop = BattleProps.globalPropPool.getProp(gagDef.prop)
        except Exception:
            prop = None
        if prop is None or prop.isEmpty():
            return
        prop.reparentTo(render)
        prop.setPos(x, y, z)
        prop.setScale(0.01)
        if gagDef.prop == 'trapdoor':
            prop.setScale(0.01)
        appear = Sequence(LerpScaleInterval(prop, 0.3, 1.0 if gagDef.prop != 'tnt' else 1.2, blendType='easeOut'),
                          name=self.uniqueName('trapAppear-%d' % trapId))
        self._addActionTrack(appear)
        sfx = globalBattleSoundCache.getSound(gagDef.sound)
        if sfx:
            base.playSfx(sfx, node=prop)
        self.actionTraps[trapId] = (prop, gagDef)
        messenger.send('action-trap-placed', [trapId, avId, level])

    def actionTrapTriggered(self, trapId, suitId):
        entry = self.actionTraps.get(trapId)
        if entry is None:
            return
        prop, gagDef = entry
        if suitId:
            pos = prop.getPos(render)
            explosion = MovieUtil.createKapowExplosionTrack(render, explosionPoint=Point3(pos[0], pos[1], pos[2] + 1.5),
                                                            scale=1.0 + gagDef.splash * 0.15)
            sfx = globalBattleSoundCache.getSound('TL_step_on_rake.ogg' if gagDef.prop == 'banana' else gagDef.sound)
            if sfx:
                base.playSfx(sfx, node=prop)
            self._addActionTrack(explosion)
        self._removeActionTrap(trapId)
        messenger.send('action-trap-triggered', [trapId, suitId])

    def _removeActionTrap(self, trapId):
        entry = self.actionTraps.pop(trapId, None)
        if entry is not None:
            prop = entry[0]
            if prop is not None and not prop.isEmpty():
                MovieUtil.removeProp(prop)

    def _clearActionTraps(self):
        for trapId in list(self.actionTraps.keys()):
            self._removeActionTrap(trapId)

    def d_suitListQuery(self):
        self.sendUpdate('suitListQuery')

    def suitListResponse(self, suitList):
        self.suitList = suitList
        messenger.send('suitListResponse')

    def d_buildingListQuery(self):
        self.sendUpdate('buildingListQuery')

    def buildingListResponse(self, buildingList):
        self.buildingList = buildingList
        messenger.send('buildingListResponse')

    def hidePaths(self):
        if self.pathViz:
            self.pathViz.detachNode()
            self.pathViz = None
        return

    def showPaths(self):
        self.hidePaths()
        vizNode = GeomNode(self.uniqueName('PathViz'))
        lines = LineSegs()
        self.pathViz = render.attachNewNode(vizNode)
        points = self.frontdoorPointList + self.sidedoorPointList + self.cogHQDoorPointList + self.streetPointList
        while len(points) > 0:
            self.__doShowPoints(vizNode, lines, None, points)

        cnode = CollisionNode('battleCells')
        cnode.setCollideMask(BitMask32.allOff())
        for zoneId, cellPos in list(self.battlePosDict.items()):
            cnode.addSolid(CollisionSphere(cellPos, 9))
            text = '%s' % zoneId
            self.__makePathVizText(text, cellPos[0], cellPos[1], cellPos[2] + 9, (1, 1, 1, 1))

        self.pathViz.attachNewNode(cnode).show()
        return

    def __doShowPoints(self, vizNode, lines, p, points):
        if p == None:
            pi = len(points) - 1
            if pi < 0:
                return
            p = points[pi]
            del points[pi]
        else:
            if p not in points:
                return
            pi = points.index(p)
            del points[pi]
        text = '%s' % p.getIndex()
        pos = p.getPos()
        if p.getPointType() == DNASuitPoint.FRONTDOORPOINT:
            color = (1, 0, 0, 1)
        elif p.getPointType() == DNASuitPoint.SIDEDOORPOINT:
            color = (0, 0, 1, 1)
        else:
            color = (0, 1, 0, 1)
        self.__makePathVizText(text, pos[0], pos[1], pos[2], color)
        adjacent = self.dnaStore.getAdjacentPoints(p)
        numPoints = adjacent.getNumPoints()
        for i in range(numPoints):
            qi = adjacent.getPointIndex(i)
            q = self.dnaStore.getSuitPointWithIndex(qi)
            pp = p.getPos()
            qp = q.getPos()
            v = Vec3(qp - pp)
            v.normalize()
            c = v.cross(Vec3.up())
            p1a = pp + v * 2 + c * 0.5
            p1b = pp + v * 3
            p1c = pp + v * 2 - c * 0.5
            lines.reset()
            lines.moveTo(pp)
            lines.drawTo(qp)
            lines.moveTo(p1a)
            lines.drawTo(p1b)
            lines.drawTo(p1c)
            lines.create(vizNode, 0)
            self.__doShowPoints(vizNode, lines, q, points)

        return

    def __makePathVizText(self, text, x, y, z, color):
        if not hasattr(self, 'debugTextNode'):
            self.debugTextNode = TextNode('debugTextNode')
            self.debugTextNode.setAlign(TextNode.ACenter)
            self.debugTextNode.setFont(ToontownGlobals.getSignFont())
        self.debugTextNode.setTextColor(*color)
        self.debugTextNode.setText(text)
        np = self.pathViz.attachNewNode(self.debugTextNode.generate())
        np.setPos(x, y, z + 1)
        np.setScale(1.0)
        np.setBillboardPointEye(2)
        np.node().setAttrib(TransparencyAttrib.make(TransparencyAttrib.MDual), 2)
