from panda3d.core import *
from direct.distributed import DistributedObject
from direct.directnotify import DirectNotifyGlobal
from direct.task import Task
from toontown.hood import ZoneUtil
from toontown.toonbase import ToontownGlobals

class TutorialManager(DistributedObject.DistributedObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('TutorialManager')
    neverDisable = 1

    def __init__(self, cr):
        DistributedObject.DistributedObject.__init__(self, cr)
        self._leavingTutorial = False
        self._leaveAttempts = 0

    def generate(self):
        DistributedObject.DistributedObject.generate(self)
        messenger.send('tmGenerate')
        self.accept('requestTutorial', self.d_requestTutorial)
        self.accept('requestSkipTutorial', self.d_requestSkipTutorial)
        self.accept('rejectTutorial', self.d_rejectTutorial)
        self.accept('action-tutorial-finished', self.__handleActionTutorialFinished)

    def disable(self):
        self.ignoreAll()
        ZoneUtil.overrideOff()
        DistributedObject.DistributedObject.disable(self)

    def d_requestTutorial(self):
        self.sendUpdate('requestTutorial', [])

    def d_rejectTutorial(self):
        self.sendUpdate('rejectTutorial', [])

    def d_requestSkipTutorial(self):
        self.sendUpdate('requestSkipTutorial', [])

    def skipTutorialResponse(self, allOk):
        messenger.send('skipTutorialAnswered', [allOk])

    def enterTutorial(self, branchZone, streetZone, shopZone, hqZone):
        base.localAvatar.cantLeaveGame = 1
        ZoneUtil.overrideOn(branch=branchZone, exteriorList=[streetZone], interiorList=[shopZone, hqZone])
        messenger.send('startTutorial', [shopZone])
        self.acceptOnce('stopTutorial', self.__handleStopTutorial)
        self.acceptOnce('toonArrivedTutorial', self.d_toonArrived)

    def __handleStopTutorial(self):
        base.localAvatar.cantLeaveGame = 0
        self.d_allDone()
        ZoneUtil.overrideOff()

    def __handleActionTutorialFinished(self, success):
        """Return the Toon to the playground once the guided tutorial is over."""
        self.notify.info('action tutorial finished (success=%s)' % success)
        if self._leavingTutorial:
            return
        self._leavingTutorial = True
        self._leaveAttempts = 0
        self.__leaveTutorial()

    def __leaveTutorial(self):
        """Teleport to the Toon's playground, then release the private zones.

        The tutorial street is a normal ``Street``, so its own ``teleportOut``
        state does the zoning work; the temporary zones are only deallocated
        once the client has arrived somewhere real, so a slow load cannot leave
        the Toon standing in a deallocated zone.
        """
        place = None
        try:
            place = base.cr.playGame.getPlace()
        except Exception:
            place = None
        if place is None or not hasattr(place, 'fsm'):
            self.notify.warning('no place to leave the tutorial from')
            messenger.send('stopTutorial')
            return
        # Some states (shops, the sticker book) cannot teleport straight out.
        try:
            if place.fsm.getCurrentState().getName() not in ('walk', 'tierSelect',
                                                             'stopped', 'teleportOut'):
                place.fsm.request('walk')
        except Exception:
            pass
        hoodId = getattr(base.localAvatar, 'defaultZone', 0)
        if not hoodId or hoodId == ToontownGlobals.Tutorial:
            hoodId = ToontownGlobals.ToontownCentral
        requestStatus = {'loader': ZoneUtil.getLoaderName(hoodId),
                         'where': ZoneUtil.getWhereName(hoodId, 1),
                         'how': 'teleportIn',
                         'hoodId': hoodId,
                         'zoneId': hoodId,
                         'shardId': None,
                         'avId': -1}
        try:
            place.fsm.request('teleportOut', [requestStatus])
        except Exception:
            self._leaveAttempts += 1
            if self._leaveAttempts < 3:
                self.notify.warning('retrying tutorial exit (%d)' % self._leaveAttempts)
                taskMgr.doMethodLater(0.5, self.__retryLeave, 'actionTutorialRetry')
                return
            self.notify.warning('could not teleport out of the tutorial')
            messenger.send('stopTutorial')
            return
        # Let the teleport settle before tearing the private zones down.
        taskMgr.doMethodLater(1.5, self.__finishTutorial, 'actionTutorialStop')

    def __retryLeave(self, task):
        self.__leaveTutorial()
        return Task.done

    def __finishTutorial(self, task):
        messenger.send('stopTutorial')
        return Task.done

    def d_allDone(self):
        self.sendUpdate('allDone', [])

    def d_toonArrived(self):
        self.sendUpdate('toonArrived', [])
