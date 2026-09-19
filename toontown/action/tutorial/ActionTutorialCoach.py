"""The on-screen coach for the guided tutorial.

A compact panel in the bottom-left corner: the step counter and title, the
step-specific hint, a progress bar, a Continue button on the explanatory steps
and a Skip Tutorial button that is always available.
"""

from panda3d.core import TextNode, Vec4
from direct.gui.DirectGui import DirectButton, DirectFrame, DirectWaitBar, DGG, OnscreenText
from direct.showbase.DirectObject import DirectObject

from toontown.action.tutorial import ActionTutorialGlobals as G
from toontown.toonbase import TTLocalizer, ToontownGlobals


def _stepName(step):
    names = TTLocalizer.ActionTutorialStepNames
    if 0 <= step < len(names):
        return names[step]
    return ''


def _stepHint(step):
    hints = TTLocalizer.ActionTutorialHints
    if 0 <= step < len(hints):
        return hints[step]
    return ''


class ActionTutorialCoach(DirectObject):

    def __init__(self, step=G.STEP_WELCOME, total=G.NUM_STEPS):
        DirectObject.__init__(self)
        self.step = step
        self.total = total
        self.value = G.NO_VALUE
        self.signFont = ToontownGlobals.getSignFont()
        self.uiFont = ToontownGlobals.getInterfaceFont()
        self._build()
        self.setStep(step, total)

    # ------------------------------------------------------------------
    def _build(self):
        self.frame = DirectFrame(parent=base.a2dBottomLeft, relief=DGG.FLAT,
                                 frameSize=(-0.02, 0.72, -0.02, 0.5), frameColor=(0.06, 0.06, 0.1, 0.85),
                                 pos=(0.03, 0, 0.06))
        self.frame.setBin('gui-popup', 80)
        self.title = OnscreenText(parent=self.frame, text=TTLocalizer.ActionTutorialTitle, pos=(-0.005, 0.44),
                                  scale=0.05, fg=(1, 0.85, 0.35, 1), shadow=(0, 0, 0, 1),
                                  align=TextNode.ALeft, font=self.signFont)
        self.progressText = OnscreenText(parent=self.frame, text='', pos=(-0.005, 0.365), scale=0.04,
                                         fg=(0.85, 0.9, 1.0, 1), shadow=(0, 0, 0, 1),
                                         align=TextNode.ALeft, font=self.uiFont, mayChange=True)
        self.progressBar = DirectWaitBar(parent=self.frame, relief=DGG.FLAT, range=1.0, value=0.0,
                                         frameSize=(-0.01, 0.66, 0.335, 0.355), pos=(0, 0, 0),
                                         frameColor=(0.05, 0.05, 0.08, 0.9), barColor=(0.4, 0.75, 0.4, 0.95),
                                         text='')
        self.stepName = OnscreenText(parent=self.frame, text='', pos=(-0.005, 0.27), scale=0.055,
                                     fg=(1, 1, 1, 1), shadow=(0, 0, 0, 1),
                                     align=TextNode.ALeft, font=self.signFont, mayChange=True)
        self.hint = OnscreenText(parent=self.frame, text='', pos=(-0.005, 0.17), scale=0.038,
                                 fg=(0.9, 0.92, 0.95, 1), shadow=(0, 0, 0, 1),
                                 align=TextNode.ALeft, font=self.uiFont, mayChange=True, wordwrap=30)
        self.valueText = OnscreenText(parent=self.frame, text='', pos=(-0.005, 0.075), scale=0.04,
                                      fg=(0.6, 1.0, 0.6, 1), shadow=(0, 0, 0, 1),
                                      align=TextNode.ALeft, font=self.uiFont, mayChange=True)

        self.continueButton = DirectButton(parent=self.frame, relief=DGG.FLAT,
                                           frameSize=(-0.14, 0.14, -0.045, 0.045),
                                           frameColor=(0.25, 0.6, 0.3, 0.95), text=TTLocalizer.ActionTutorialContinue,
                                           text_scale=0.045, text_pos=(0, -0.014), text_fg=(1, 1, 1, 1),
                                           text_font=self.signFont, pos=(0.14, 0, 0.0),
                                           command=self.__advance)
        self.skipButton = DirectButton(parent=self.frame, relief=DGG.FLAT,
                                       frameSize=(-0.14, 0.14, -0.045, 0.045),
                                       frameColor=(0.5, 0.3, 0.25, 0.95), text=TTLocalizer.ActionTutorialSkip,
                                       text_scale=0.04, text_pos=(0, -0.012), text_fg=(1, 1, 1, 1),
                                       text_font=self.signFont, pos=(0.46, 0, 0.0),
                                       command=self.__skip)

    # ------------------------------------------------------------------
    def destroy(self):
        self.ignoreAll()
        if self.frame:
            self.frame.destroy()
            self.frame = None

    # ------------------------------------------------------------------
    def setStep(self, step, total):
        self.step = step
        self.total = max(1, total)
        self.progressText.setText(TTLocalizer.ActionTutorialProgress % (min(step + 1, self.total), self.total))
        self.progressBar['value'] = min(1.0, float(step) / float(self.total))
        self.stepName.setText(_stepName(step))
        self.hint.setText(_stepHint(step))
        if G.stepIsExplanatory(step):
            self.continueButton.show()
        else:
            self.continueButton.hide()

    def setValue(self, value):
        self.value = value
        if self.step == G.STEP_LOADOUT:
            self.valueText.setText(TTLocalizer.ActionTutorialLoadoutValue % (int(value), G.LOADOUT_TARGET))
        elif self.step == G.STEP_DODGE:
            self.valueText.setText(TTLocalizer.ActionTutorialDodgeValue % (G.DODGE_ATTEMPTS - int(value)))
        else:
            self.valueText.setText('')

    def showStepComplete(self, step, beans):
        self.valueText.setFg((0.6, 1.0, 0.6, 1))
        self.valueText.setText(TTLocalizer.ActionTutorialStepBeans % beans)

    def finish(self, success):
        if self.frame is None:
            return
        self.stepName.setText(TTLocalizer.ActionTutorialComplete if success else TTLocalizer.ActionTutorialSkipped)
        self.hint.setText('')
        self.valueText.setText('')
        self.progressBar['value'] = 1.0
        self.continueButton.hide()
        self.skipButton.hide()

    # ------------------------------------------------------------------
    def __advance(self):
        messenger.send('action-tutorial-advance')

    def __skip(self):
        messenger.send('action-tutorial-skip')
