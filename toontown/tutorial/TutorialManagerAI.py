"""AI entry point for the new-Toon tutorial.

A Toon that finishes Make-A-Toon is routed into a private tutorial street.  The
legacy turn-based battle was replaced by the guided real-time combat tutorial
in :mod:`toontown.action.tutorial`: this manager still owns the temporary
zones and the lifecycle, but the actual lesson is run by
:class:`~toontown.action.tutorial.ActionTutorialManagerAI.ActionTutorialManagerAI`.

Flow::

    requestTutorial   allocate the private zones
    startTutorial     client loads the tutorial street
    toonArrived       normalise the Toon, then hand off to the action tutorial
    allDone           client finished (or skipped) -> deallocate

The player may skip at any point; a skipped tutorial still sets ``tutorialAck``
so it is not offered again.
"""

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectAI import DistributedObjectAI
from direct.fsm.FSM import FSM


class TutorialZones:
    BRANCH = 0
    STREET = 0
    SHOP = 0
    HQ = 0


class TutorialFSM(FSM):
    """Owns the temporary zones for one Toon's tutorial."""

    notify = DirectNotifyGlobal.directNotify.newCategory('TutorialFSM')

    def __init__(self, air, zones, avId):
        FSM.__init__(self, 'TutorialFSM')
        self.air = air
        self.zones = zones
        self.avId = avId
        self.forceTransition('Action')

    def enterAction(self):
        # The action tutorial starts once the Toon has actually arrived, in
        # TutorialManagerAI.toonArrived().
        pass

    def exitAction(self):
        pass

    def enterCleanup(self):
        manager = getattr(self.air, 'actionTutorialManager', None)
        if manager is not None:
            manager.endSession(self.avId)
        for zone in (self.zones.BRANCH, self.zones.STREET, self.zones.SHOP, self.zones.HQ):
            self.air.deallocateZone(zone)
        fsms = getattr(self.air.tutorialManager, 'avId2fsm', None)
        if fsms is not None and self.avId in fsms:
            del fsms[self.avId]


class TutorialManagerAI(DistributedObjectAI):
    notify = DirectNotifyGlobal.directNotify.newCategory('TutorialManagerAI')

    def __init__(self, air):
        DistributedObjectAI.__init__(self, air)
        self.avId2fsm = {}

    def requestTutorial(self):
        avId = self.air.getAvatarIdFromSender()
        if not avId:
            return

        zones = TutorialZones()
        zones.BRANCH = self.air.allocateZone()
        zones.STREET = self.air.allocateZone()
        zones.SHOP = self.air.allocateZone()
        zones.HQ = self.air.allocateZone()

        self.avId2fsm[avId] = TutorialFSM(self.air, zones, avId)

        event = self.air.getAvatarExitEvent(avId)
        self.acceptOnce(event, self.__unexpectedExit, extraArgs=[avId])

        self.d_enterTutorial(avId, zones.STREET, zones.STREET, zones.SHOP, zones.HQ)

    def __unexpectedExit(self, avId):
        fsm = self.avId2fsm.get(avId)
        if fsm:
            fsm.demand('Cleanup')

    def rejectTutorial(self):
        # This is never used by the client.
        pass

    def requestSkipTutorial(self):
        avId = self.air.getAvatarIdFromSender()
        av = self.air.doId2do.get(avId)
        if av:
            av.b_setTutorialAck(1)
            self.d_skipTutorialResponse(avId, 1)
        else:
            self.d_skipTutorialResponse(avId, 0)

    def d_skipTutorialResponse(self, avId, allOk):
        self.sendUpdateToAvatarId(avId, 'skipTutorialResponse', [allOk])

    def d_enterTutorial(self, avId, branchZone, streetZone, shopZone, hqZone):
        self.sendUpdateToAvatarId(avId, 'enterTutorial', [branchZone, streetZone, shopZone, hqZone])

    def toonArrived(self):
        avId = self.air.getAvatarIdFromSender()
        av = self.air.doId2do.get(avId)
        if not av:
            return

        fsm = self.avId2fsm.get(avId)
        if av.getTutorialAck():
            if fsm:
                fsm.demand('Cleanup')
            self.air.writeServerEvent('suspicious', avId,
                                      'Attempted to request tutorial when it would be impossible to do so')
            return

        # Reset Toon so that their stats are appropriate for the tutorial.
        av.b_setQuests([])
        av.b_setQuestHistory([])
        av.b_setRewardHistory(0, [])
        av.b_setHp(15)
        av.b_setMaxHp(15)
        av.inventory.maxInventory()
        av.d_setInventory(av.inventory.makeNetString())
        av.experience.zeroOutExp()
        av.d_setExperience(av.experience.getCurrentExperience())

        # Hand off to the real-time combat tutorial.
        manager = getattr(self.air, 'actionTutorialManager', None)
        if fsm is not None and manager is not None:
            manager.startSession(avId, fsm.zones.STREET)

    def allDone(self):
        avId = self.air.getAvatarIdFromSender()
        av = self.air.doId2do.get(avId)
        if av:
            av.b_setTutorialAck(1)

        event = self.air.getAvatarExitEvent(avId)
        self.ignore(event)

        fsm = self.avId2fsm.get(avId)
        if fsm:
            fsm.demand('Cleanup')
        else:
            self.air.writeServerEvent('suspicious', avId, 'Attempted to exit a non-existent tutorial.')
