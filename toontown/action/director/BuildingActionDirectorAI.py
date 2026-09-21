"""Real-time combat director for a Cog building floor.

The regular street director owns roaming population and extraction pressure.
This director deliberately owns only one interior floor: all Cogs are created
by the existing building planner, registered here together, and immediately
aggro the Toons in the room.  That lets the building keep its established
elevator/zone protocol while the fight itself runs on the same movement,
telegraph, and gag rules as a street.

Difficulty scales with building depth: each floor raises the effective tier
so a five-storey building climaxes on the top-floor office, independent of
the tier the Toons used outside.
"""

from panda3d.core import Point3
from direct.directnotify import DirectNotifyGlobal
from direct.task import Task

from toontown.action import ActionGlobals, ActionProgression
from toontown.action.director.StreetDirectorAI import StreetDirectorAI


class BuildingActionDirectorAI(StreetDirectorAI):
    notify = DirectNotifyGlobal.directNotify.newCategory('BuildingActionDirectorAI')

    # Effective tier added per floor above the ground level.
    TIER_PER_FLOOR = 1.5
    # Flat bean bonus granted to every living Toon when a floor is cleared.
    FLOOR_CLEAR_BEANS_BASE = 25
    FLOOR_CLEAR_BEANS_PER_FLOOR = 12
    # Bonus for reaching and clearing the top-floor office.
    TOP_FLOOR_BONUS_BASE = 60
    TOP_FLOOR_BONUS_PER_FLOOR = 30

    def __init__(self, interior, endpoint):
        # Reuse the authoritative gag validation, reward, trap, and Cog
        # controller integration.  ``endpoint`` is the street planner object;
        # its client mirror already owns the action protocol used by the HUD.
        StreetDirectorAI.__init__(self, endpoint)
        self.interior = interior
        self.endpoint = endpoint
        self.floor = 0
        self.floorCallback = None
        self.floorFinished = False
        self.floorKills = 0
        self.floorStartTime = 0.0
        self.taskName = 'actionBuilding-%s' % id(self)

    def startFloor(self, floor, toonIds, suits, callback):
        self.floor = floor
        self.floorCallback = callback
        self.floorFinished = False
        self.floorKills = 0
        self.activeToons = {}
        self.safeToons = set()
        now = globalClock.getFrameTime()
        self.floorStartTime = now
        powers = []
        for toonId in toonIds:
            toon = self.air.doId2do.get(toonId)
            if toon is not None:
                self.activeToons[toonId] = now
                powers.append(ActionProgression.getPowerRating(toon))
        power = sum(powers) / len(powers) if powers else ActionGlobals.MIN_POWER
        # Difficulty climbs with building depth so a top-floor office fights
        # like a much tougher street than the lobby of the same building.
        effectiveTier = ActionGlobals.DEFAULT_TIER + self.TIER_PER_FLOOR * max(0, floor)
        self.tier = max(ActionGlobals.MIN_TIER,
                        min(ActionGlobals.MAX_TIER, int(round(effectiveTier))))
        self.profile = ActionGlobals.getDifficultyProfile(self.tier, power, 0)
        self.runActive = bool(self.activeToons)
        self.lastTickTime = now
        for index, suit in enumerate(suits):
            controller = self.registerSuit(suit)
            if controller is None:
                continue
            # Building rooms do not have street path points.  The client uses
            # the same stable positions for the room, so seed the AI at the
            # corresponding room point and immediately take control.
            roomPositions = (
                (Point3(0, 15, 0), Point3(10, 20, 0), Point3(-7, 24, 0), Point3(-10, 0, 0)),
                (Point3(0, 18, 0), Point3(10, 12, 0), Point3(-9, 11, 0), Point3(-3, 13, 0)),
                (Point3(0, 15, 0), Point3(10, 20, 0), Point3(-10, 6, 0), Point3(-17, 34, 11)),
            )
            room = roomPositions[2 if floor == self.interior.topFloor else (0 if floor == 0 else 1)]
            pos = room[index % len(room)]
            suit.setPos(pos)
            controller.pos = Point3(pos)
            controller.havePos = True
            if self.activeToons:
                toon = self.air.doId2do.get(next(iter(self.activeToons)))
                controller.aggro(toon, now)
        taskMgr.add(self._tick, self.taskName)

    def _tick(self, task):
        if self.interior is None:
            return Task.done
        now = globalClock.getFrameTime()
        dt = max(0.0, min(0.5, now - self.lastTickTime))
        self.lastTickTime = now
        for controller in list(self.controllers.values()):
            try:
                controller.tick(now, dt, self.profile)
            except Exception:
                self.notify.warning('Building Cog controller tick failed')
        self._tickTraps(now)
        if self.runActive and self.controllers and not any(controller.isActive() for controller in self.controllers.values()):
            self._finishFloor()
        return Task.cont

    def onCogDefeated(self, suit, toon, gagDef):
        StreetDirectorAI.onCogDefeated(self, suit, toon, gagDef)
        self.floorKills += 1
        if self.controllers and not any(controller.isActive() for controller in self.controllers.values()):
            self._finishFloor()

    def _checkBreakthrough(self):
        # Building floors pay their own per-floor bonuses.  The street
        # breakthrough valve must not fire from indoors: it would drain the
        # street's pressure meter for kills made inside and spam its banner.
        return

    def _maybeSpawnCache(self, initial=False):
        # Gag caches spawn on street geometry; one planted on the street
        # while everyone fights inside would sit there unreachable.
        return

    def _getFloorClearBeans(self, floor):
        return self.FLOOR_CLEAR_BEANS_BASE + self.FLOOR_CLEAR_BEANS_PER_FLOOR * floor

    def _finishFloor(self):
        if self.floorFinished:
            return
        self.floorFinished = True
        self.runActive = False
        topFloor = self.floor == self.interior.topFloor
        if topFloor:
            bonus = self.TOP_FLOOR_BONUS_BASE + self.TOP_FLOOR_BONUS_PER_FLOOR * (self.interior.topFloor + 1)
        else:
            bonus = self._getFloorClearBeans(self.floor)
        for avId in list(self.activeToons):
            toon = self.air.doId2do.get(avId)
            if toon is not None:
                self._grantBeans(toon, bonus)
        if self.endpoint is not None:
            value = min(65535, bonus)
            if topFloor:
                self.endpoint.sendUpdate('actionAnnounce',
                                         [ActionGlobals.ANNOUNCE_BREAKTHROUGH, 0, value])
            else:
                self.endpoint.sendUpdate('actionAnnounce',
                                         [ActionGlobals.ANNOUNCE_BUILDING_FLOOR_CLEARED, 0, value])
        if self.floorCallback is not None:
            self.floorCallback()

    def stop(self):
        taskMgr.remove(self.taskName)
        for controller in list(self.controllers.values()):
            suit = controller.suit
            if suit is not None:
                suit.actionController = None
            controller.cleanup()
        self.controllers = {}
        self.activeToons = {}
        self.interior = None
        self.endpoint = None
