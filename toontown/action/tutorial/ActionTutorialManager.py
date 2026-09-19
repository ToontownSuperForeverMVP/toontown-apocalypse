"""Client mirror of the AI action tutorial.

The AI owns the step; this object listens for the replicated step/value and
drives the on-screen coach.  All player input funnels back through
``requestAdvance`` (the explanatory steps) and ``requestSkip``.
"""

from direct.distributed import DistributedObject
from direct.directnotify import DirectNotifyGlobal

from toontown.action.tutorial import ActionTutorialGlobals as G
from toontown.action.tutorial.ActionTutorialCoach import ActionTutorialCoach


class ActionTutorialManager(DistributedObject.DistributedObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('ActionTutorialManager')
    neverDisable = 1

    def __init__(self, cr):
        DistributedObject.DistributedObject.__init__(self, cr)
        self.coach = None
        self.step = G.STEP_WELCOME
        self.total = G.NUM_STEPS
        self.finished = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def generate(self):
        DistributedObject.DistributedObject.generate(self)
        self.accept('action-tutorial-advance', self.d_requestAdvance)
        self.accept('action-tutorial-skip', self.d_requestSkip)
        messenger.send('actionTutorialManagerReady', [self])

    def disable(self):
        self.ignoreAll()
        self._destroyCoach()
        DistributedObject.DistributedObject.disable(self)

    def _ensureCoach(self):
        if self.coach is None and not self.finished:
            self.coach = ActionTutorialCoach(self.step, self.total)
        return self.coach

    def _destroyCoach(self):
        if self.coach is not None:
            self.coach.destroy()
            self.coach = None

    # ------------------------------------------------------------------
    # Owner-sent requests
    # ------------------------------------------------------------------
    def d_requestAdvance(self):
        self.sendUpdate('requestAdvance', [])

    def d_requestSkip(self):
        self.sendUpdate('requestSkip', [])

    # ------------------------------------------------------------------
    # Server fields
    # ------------------------------------------------------------------
    def setTutorialStep(self, step, total):
        self.step = max(0, min(total, int(step)))
        self.total = max(1, int(total))
        coach = self._ensureCoach()
        if coach is not None:
            coach.setStep(self.step, self.total)

    def setTutorialValue(self, value):
        if self.coach is not None:
            self.coach.setValue(value)

    def tutorialStepComplete(self, step, beans):
        if self.coach is not None:
            self.coach.showStepComplete(step, beans)

    def tutorialFinished(self, success):
        if self.finished:
            return
        self.finished = True
        if self.coach is not None:
            self.coach.finish(bool(success))
        self._destroyCoach()
        messenger.send('action-tutorial-finished', [bool(success)])
