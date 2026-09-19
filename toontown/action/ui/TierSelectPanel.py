"""Difficulty tier picker shown when a Toon walks onto a street.

The panel previews what the tier means *for this Toon* (the profile is
player-relative) and remembers the last choice per street.  Arrow keys or the
number keys change the tier, Enter confirms, Escape keeps the current pick.
"""

from panda3d.core import TextNode, Vec4
from direct.gui.DirectGui import DirectButton, DirectFrame, DGG, OnscreenText
from direct.showbase.DirectObject import DirectObject

from toontown.action import ActionGlobals, ActionProgression
from toontown.toonbase import TTLocalizer, ToontownGlobals


class TierSelectPanel(DirectObject):

    def __init__(self, streetName, initialTier, doneEvent, loadoutCallback=None):
        DirectObject.__init__(self)
        self.doneEvent = doneEvent
        self.loadoutCallback = loadoutCallback
        self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(initialTier)))
        self.signFont = ToontownGlobals.getSignFont()
        self.uiFont = ToontownGlobals.getInterfaceFont()
        self.tierButtons = []
        self.finished = False
        self._build(streetName)
        self._acceptKeys()
        self._refresh()

    def _build(self, streetName):
        self.frame = DirectFrame(parent=aspect2d, relief=DGG.FLAT, frameSize=(-0.95, 0.95, -0.52, 0.52),
                                 frameColor=(0.06, 0.06, 0.1, 0.9), pos=(0, 0, 0.05))
        self.frame.setBin('gui-popup', 60)
        OnscreenText(parent=self.frame, text=TTLocalizer.ActionTierTitle, pos=(0, 0.4), scale=0.085,
                     fg=(1, 0.85, 0.35, 1), shadow=(0, 0, 0, 1), font=self.signFont)
        OnscreenText(parent=self.frame, text=streetName, pos=(0, 0.31), scale=0.055,
                     fg=(1, 1, 1, 0.9), shadow=(0, 0, 0, 1), font=self.uiFont)

        for index in range(ActionGlobals.MIN_TIER, ActionGlobals.MAX_TIER + 1):
            x = -0.81 + (index - 1) * 0.18
            button = DirectButton(parent=self.frame, relief=DGG.FLAT, frameSize=(-0.075, 0.075, -0.075, 0.075),
                                  frameColor=(0.2, 0.2, 0.25, 0.95), text=str(index), text_scale=0.07,
                                  text_pos=(0, -0.025), text_fg=(1, 1, 1, 1), text_font=self.signFont,
                                  pos=(x, 0, 0.15), command=self.__pickTier, extraArgs=[index])
            self.tierButtons.append(button)

        self.detailText = OnscreenText(parent=self.frame, text='', pos=(0, -0.02), scale=0.045,
                                       fg=(0.95, 0.95, 0.95, 1), shadow=(0, 0, 0, 1), font=self.uiFont,
                                       mayChange=True)
        self.flavorText = OnscreenText(parent=self.frame, text='', pos=(0, -0.1), scale=0.042,
                                       fg=(0.8, 0.85, 1.0, 1), shadow=(0, 0, 0, 1), font=self.uiFont,
                                       mayChange=True, wordwrap=36)
        self.powerText = OnscreenText(parent=self.frame, text='', pos=(0, -0.22), scale=0.045,
                                      fg=(0.6, 1.0, 0.6, 1), shadow=(0, 0, 0, 1), font=self.uiFont, mayChange=True)
        self.hintText = OnscreenText(parent=self.frame, text=TTLocalizer.ActionTierHint, pos=(0, -0.46), scale=0.035,
                                     fg=(0.8, 0.8, 0.8, 0.9), font=self.uiFont)

        self.confirmButton = DirectButton(parent=self.frame, relief=DGG.FLAT, frameSize=(-0.3, 0.3, -0.06, 0.06),
                                          frameColor=(0.25, 0.6, 0.3, 0.95), text=TTLocalizer.ActionTierConfirm,
                                          text_scale=0.055, text_pos=(0, -0.02), text_fg=(1, 1, 1, 1),
                                          text_font=self.signFont, pos=(0.3, 0, -0.35), command=self.confirm)
        self.loadoutButton = DirectButton(parent=self.frame, relief=DGG.FLAT, frameSize=(-0.3, 0.3, -0.06, 0.06),
                                          frameColor=(0.3, 0.3, 0.55, 0.95), text=TTLocalizer.ActionTierLoadout,
                                          text_scale=0.055, text_pos=(0, -0.02), text_fg=(1, 1, 1, 1),
                                          text_font=self.signFont, pos=(-0.3, 0, -0.35), command=self.__openLoadout)

    def _acceptKeys(self):
        self.accept('arrow_left', self.__step, [-1])
        self.accept('arrow_right', self.__step, [1])
        self.accept('arrow_down', self.__step, [-1])
        self.accept('arrow_up', self.__step, [1])
        self.accept('a', self.__step, [-1])
        self.accept('d', self.__step, [1])
        self.accept('enter', self.confirm)
        self.accept('space', self.confirm)
        self.accept('escape', self.confirm)
        for index in range(1, 10):
            self.accept(str(index), self.__pickTier, [index])
        self.accept('0', self.__pickTier, [10])
        self.accept('action-loadout-changed', self.__handleLoadoutChanged)

    def destroy(self):
        self.ignoreAll()
        if self.frame:
            self.frame.destroy()
            self.frame = None
        self.tierButtons = []

    def __step(self, delta):
        self.__pickTier(self.tier + delta)

    def __pickTier(self, tier):
        self.tier = max(ActionGlobals.MIN_TIER, min(ActionGlobals.MAX_TIER, int(tier)))
        self._refresh()

    def __handleLoadoutChanged(self, tracks=None):
        self._refresh()

    def _refresh(self):
        power = ActionGlobals.MIN_POWER
        if hasattr(base, 'localAvatar'):
            power = ActionProgression.getPowerRating(base.localAvatar)
        profile = ActionGlobals.previewTier(self.tier, power)
        for index, button in enumerate(self.tierButtons):
            tier = index + 1
            if tier == self.tier:
                t = (tier - 1) / 9.0
                button['frameColor'] = (0.4 + 0.5 * t, 0.7 - 0.5 * t, 0.25, 1.0)
                button.setScale(1.15)
            else:
                button['frameColor'] = (0.2, 0.2, 0.25, 0.95)
                button.setScale(1.0)
        self.detailText.setText(TTLocalizer.ActionTierDetails % {
            'level': profile.levelOffset, 'reward': profile.rewardScale, 'pop': profile.populationBonus,
            'mut': profile.attackMutation})
        flavorIndex = min(len(TTLocalizer.ActionTierFlavor) - 1, (self.tier - 1) // 2)
        self.flavorText.setText(TTLocalizer.ActionTierFlavor[flavorIndex])
        self.powerText.setText(TTLocalizer.ActionTierPower % int(round(power)))

    def __openLoadout(self):
        if self.loadoutCallback:
            self.loadoutCallback()

    def confirm(self):
        if self.finished:
            return
        self.finished = True
        messenger.send(self.doneEvent, [self.tier])
