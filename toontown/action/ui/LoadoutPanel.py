"""Gag Album: equip up to three discovered tracks and buy tier upgrades.

Equipping is free and never touches mastery XP, so experimenting with builds
is always safe.  Tier purchases are owner-sent requests validated by the AI;
the panel simply refreshes from the Toon's replicated fields.
"""

from panda3d.core import TextNode, Vec4
from direct.gui.DirectGui import DirectButton, DirectFrame, DirectWaitBar, DGG, OnscreenText
from direct.showbase.DirectObject import DirectObject
from direct.task import Task

from toontown.action import ActionGlobals, ActionProgression
from toontown.toonbase import TTLocalizer, ToontownGlobals


class LoadoutPanel(DirectObject):

    ROW_HEIGHT = 0.125

    def __init__(self, doneEvent):
        DirectObject.__init__(self)
        self.doneEvent = doneEvent
        self.signFont = ToontownGlobals.getSignFont()
        self.uiFont = ToontownGlobals.getInterfaceFont()
        self.rows = []
        self.pendingLoadout = None
        self.statusTrack = None
        self.finished = False
        self._build()
        self.accept('escape', self.close)
        self.accept('action-loadout-changed', self.__handleLoadoutChanged)
        self.accept('action-gag-tier-purchased', self.__handlePurchase)
        if hasattr(base, 'localAvatar'):
            self.accept(base.localAvatar.uniqueName('moneyChange'), self.__handleMoneyChange)
        taskMgr.doMethodLater(0.5, self.__refreshTask, 'loadoutPanelRefresh')
        self.refresh()

    # ------------------------------------------------------------------
    def _build(self):
        self.frame = DirectFrame(parent=aspect2d, relief=DGG.FLAT, frameSize=(-1.05, 1.05, -0.72, 0.72),
                                 frameColor=(0.06, 0.06, 0.1, 0.94), pos=(0, 0, 0))
        self.frame.setBin('gui-popup', 70)
        OnscreenText(parent=self.frame, text=TTLocalizer.ActionLoadoutTitle, pos=(0, 0.62), scale=0.085,
                     fg=(1, 0.85, 0.35, 1), shadow=(0, 0, 0, 1), font=self.signFont)
        OnscreenText(parent=self.frame, text=TTLocalizer.ActionLoadoutSubtitle % ActionGlobals.MAX_EQUIPPED_TRACKS,
                     pos=(0, 0.545), scale=0.04, fg=(0.9, 0.9, 0.9, 0.9), font=self.uiFont)

        top = 0.46
        for track in ActionGlobals.ALL_TRACKS:
            y = top - track * self.ROW_HEIGHT
            r, g, b = ActionGlobals.TRACK_COLORS[track]
            rowFrame = DirectFrame(parent=self.frame, relief=DGG.FLAT, frameSize=(-1.0, 1.0, -0.052, 0.052),
                                   frameColor=(r * 0.25, g * 0.25, b * 0.25, 0.75), pos=(0, 0, y))
            name = OnscreenText(parent=rowFrame, text=TTLocalizer.ActionTrackNames[track], pos=(-0.96, -0.015),
                                scale=0.05, fg=(r, g, b, 1), shadow=(0, 0, 0, 1), align=TextNode.ALeft,
                                font=self.signFont)
            tierText = OnscreenText(parent=rowFrame, text='', pos=(-0.62, -0.015), scale=0.042,
                                    fg=(1, 1, 1, 0.95), align=TextNode.ALeft, font=self.uiFont, mayChange=True)
            xpBar = DirectWaitBar(parent=rowFrame, relief=DGG.FLAT, range=1.0, value=0.0,
                                  frameSize=(-0.32, 0.12, -0.018, 0.018), pos=(0, 0, -0.005),
                                  frameColor=(0.05, 0.05, 0.08, 0.9), barColor=(r, g, b, 0.9), text='')
            xpText = OnscreenText(parent=rowFrame, text='', pos=(-0.1, 0.02), scale=0.03, fg=(1, 1, 1, 0.9),
                                  font=self.uiFont, mayChange=True)
            buyButton = DirectButton(parent=rowFrame, relief=DGG.FLAT, frameSize=(-0.19, 0.19, -0.045, 0.045),
                                     frameColor=(0.65, 0.5, 0.15, 0.95), text='', text_scale=0.03,
                                     text_pos=(0, -0.008), text_fg=(1, 1, 1, 1), text_font=self.uiFont,
                                     pos=(0.42, 0, 0), command=self.__buy, extraArgs=[track])
            equipButton = DirectButton(parent=rowFrame, relief=DGG.FLAT, frameSize=(-0.17, 0.17, -0.045, 0.045),
                                       frameColor=(0.25, 0.45, 0.7, 0.95), text='', text_scale=0.04,
                                       text_pos=(0, -0.013), text_fg=(1, 1, 1, 1), text_font=self.signFont,
                                       pos=(0.8, 0, 0), command=self.__toggleEquip, extraArgs=[track])
            self.rows.append({'frame': rowFrame, 'tier': tierText, 'xpBar': xpBar, 'xpText': xpText,
                              'buy': buyButton, 'equip': equipButton})

        self.beanText = OnscreenText(parent=self.frame, text='', pos=(-0.98, -0.5), scale=0.045,
                                     fg=(1, 0.85, 0.35, 1), shadow=(0, 0, 0, 1), align=TextNode.ALeft,
                                     font=self.uiFont, mayChange=True)
        self.powerText = OnscreenText(parent=self.frame, text='', pos=(-0.98, -0.57), scale=0.045,
                                      fg=(0.6, 1.0, 0.6, 1), shadow=(0, 0, 0, 1), align=TextNode.ALeft,
                                      font=self.uiFont, mayChange=True)
        self.statusText = OnscreenText(parent=self.frame, text='', pos=(0.1, -0.54), scale=0.04,
                                       fg=(1, 1, 1, 1), shadow=(0, 0, 0, 1), font=self.uiFont, mayChange=True)
        self.closeButton = DirectButton(parent=self.frame, relief=DGG.FLAT, frameSize=(-0.22, 0.22, -0.055, 0.055),
                                        frameColor=(0.5, 0.25, 0.25, 0.95), text=TTLocalizer.ActionLoadoutClose,
                                        text_scale=0.05, text_pos=(0, -0.017), text_fg=(1, 1, 1, 1),
                                        text_font=self.signFont, pos=(0.78, 0, -0.62), command=self.close)

    def destroy(self):
        taskMgr.remove('loadoutPanelRefresh')
        self.ignoreAll()
        if self.frame:
            self.frame.destroy()
            self.frame = None
        self.rows = []

    # ------------------------------------------------------------------
    def __refreshTask(self, task):
        self.refresh()
        return Task.again

    def __handleLoadoutChanged(self, tracks=None):
        self.pendingLoadout = None
        self.refresh()

    def __handleMoneyChange(self, money):
        self.refresh()

    def __handlePurchase(self, track, tier, success):
        name = TTLocalizer.ActionTrackNames[track] if 0 <= track < len(TTLocalizer.ActionTrackNames) else ''
        if success:
            self.statusText.setText(TTLocalizer.ActionPurchaseSuccess % (name, TTLocalizer.ActionRoman[min(tier, 3)]))
            self.statusText.setFg((0.6, 1.0, 0.6, 1))
        else:
            self.statusText.setText(TTLocalizer.ActionPurchaseFail % name)
            self.statusText.setFg((1.0, 0.6, 0.6, 1))
        self.refresh()

    def _currentLoadout(self):
        if self.pendingLoadout is not None:
            return list(self.pendingLoadout)
        return ActionProgression.getEquippedTracks(base.localAvatar)

    def refresh(self):
        if not hasattr(base, 'localAvatar') or self.frame is None:
            return
        toon = base.localAvatar
        loadout = self._currentLoadout()
        money = toon.getMoney()
        for track, row in enumerate(self.rows):
            tier = ActionProgression.getTrackTier(toon, track)
            xp = ActionProgression.getTrackXp(toon, track)
            equipButton = row['equip']
            buyButton = row['buy']
            if tier <= 0:
                row['tier'].setText(TTLocalizer.ActionLoadoutLocked)
                row['tier'].setFg((0.6, 0.6, 0.6, 0.9))
                row['xpBar']['value'] = 0.0
                row['xpText'].setText('')
                buyButton.hide()
                equipButton.hide()
                row['frame']['frameColor'] = (0.12, 0.12, 0.14, 0.6)
                continue
            r, g, b = ActionGlobals.TRACK_COLORS[track]
            row['frame']['frameColor'] = (r * 0.25, g * 0.25, b * 0.25, 0.75)
            roman = TTLocalizer.ActionRoman[min(tier, ActionGlobals.MAX_TRACK_TIER)]
            row['tier'].setText(TTLocalizer.ActionLoadoutTier % roman)
            row['tier'].setFg((1, 1, 1, 0.95))

            nextXp = ActionGlobals.getNextTierXpRequirement(tier)
            if nextXp is None:
                row['xpBar']['value'] = ActionGlobals.getMasteryFraction(xp)
                row['xpText'].setText(TTLocalizer.ActionLoadoutMastery % (xp, ActionGlobals.MAX_TRACK_XP))
                buyButton.hide()
            else:
                row['xpBar']['value'] = min(1.0, xp / float(nextXp)) if nextXp else 1.0
                row['xpText'].setText(TTLocalizer.ActionLoadoutXp % (xp, nextXp))
                allowed, reason = ActionGlobals.canPurchaseNextTier(tier, xp, money)
                cost = ActionGlobals.getNextTierCost(tier)
                nextRoman = TTLocalizer.ActionRoman[min(tier + 1, ActionGlobals.MAX_TRACK_TIER)]
                buyButton.show()
                if allowed:
                    buyButton['text'] = TTLocalizer.ActionLoadoutBuy % (nextRoman, cost)
                    buyButton['frameColor'] = (0.65, 0.5, 0.15, 0.95)
                    buyButton['state'] = DGG.NORMAL
                elif reason == 'xp':
                    buyButton['text'] = TTLocalizer.ActionLoadoutNeedXp % nextXp
                    buyButton['frameColor'] = (0.3, 0.3, 0.32, 0.9)
                    buyButton['state'] = DGG.DISABLED
                else:
                    buyButton['text'] = TTLocalizer.ActionLoadoutBuy % (nextRoman, cost)
                    buyButton['frameColor'] = (0.45, 0.3, 0.2, 0.9)
                    buyButton['state'] = DGG.DISABLED

            equipButton.show()
            if track in loadout:
                slot = loadout.index(track) + 1
                equipButton['text'] = TTLocalizer.ActionLoadoutUnequip % slot
                equipButton['frameColor'] = (0.2, 0.6, 0.35, 0.95)
                equipButton['state'] = DGG.NORMAL
            elif len(loadout) >= ActionGlobals.MAX_EQUIPPED_TRACKS:
                equipButton['text'] = TTLocalizer.ActionLoadoutFull
                equipButton['frameColor'] = (0.3, 0.3, 0.32, 0.9)
                equipButton['state'] = DGG.DISABLED
            else:
                equipButton['text'] = TTLocalizer.ActionLoadoutEquip
                equipButton['frameColor'] = (0.25, 0.45, 0.7, 0.95)
                equipButton['state'] = DGG.NORMAL

        self.beanText.setText(TTLocalizer.ActionLoadoutBeans % money)
        self.powerText.setText(TTLocalizer.ActionTierPower % int(round(ActionProgression.getPowerRating(toon))))

    # ------------------------------------------------------------------
    def __toggleEquip(self, track):
        loadout = self._currentLoadout()
        if track in loadout:
            loadout.remove(track)
        elif len(loadout) < ActionGlobals.MAX_EQUIPPED_TRACKS:
            loadout.append(track)
        else:
            return
        self.pendingLoadout = loadout
        base.localAvatar.d_requestEquipTracks(loadout)
        self.refresh()

    def __buy(self, track):
        base.localAvatar.d_requestBuyGagTier(track)

    def close(self):
        if self.finished:
            return
        self.finished = True
        messenger.send(self.doneEvent)
