"""First-person street HUD.

Street combat feedback is driven by messenger events. Health readouts use
replicated Toon and Cog fields without changing gameplay state:

* top-left    street name, tier, Cog Pressure meter with the stage name
* top-right   the current contract (procedural objectives), kept clear of
              the friends list panel that owns that corner
* bottom      the three gag slots with cooldown sweep
* centre      hit marker, damage vignette, stage banners, reward toasts and
              the "NEW TRACK DISCOVERED" ceremony
"""

from panda3d.core import TextNode, Vec4, LineSegs, TransparencyAttrib
from direct.gui.DirectGui import DirectFrame, DirectWaitBar, DGG, OnscreenText
from direct.interval.IntervalGlobal import (Func, LerpColorScaleInterval, LerpPosInterval, LerpScaleInterval,
                                             Parallel, Sequence, Wait)
from direct.showbase.DirectObject import DirectObject
from direct.task import Task

from toontown.action import ActionGlobals, ActionProgression
from toontown.action.ui.CombatAudio import CombatAudio
from toontown.toonbase import TTLocalizer, ToontownGlobals


def _trackColor(track, alpha=1.0):
    if 0 <= track < len(ActionGlobals.TRACK_COLORS):
        r, g, b = ActionGlobals.TRACK_COLORS[track]
        return Vec4(r, g, b, alpha)
    return Vec4(1, 1, 1, alpha)


def describeObjective(objective):
    """Human readable contract line for an Objective."""
    kind = objective.kind
    fmt = TTLocalizer.ActionObjectiveText.get(kind, '%(target)d')
    data = {'target': objective.target, 'param': objective.param, 'dept': '', 'stage': '', 'track': ''}
    if kind == ActionGlobals.OBJ_DEFEAT_DEPT and objective.param < len(TTLocalizer.ActionDeptNames):
        data['dept'] = TTLocalizer.ActionDeptNames[objective.param]
    elif kind == ActionGlobals.OBJ_REACH_STAGE and objective.param < len(TTLocalizer.ActionStageNames):
        data['stage'] = TTLocalizer.ActionStageNames[objective.param]
    elif kind == ActionGlobals.OBJ_HITS_WITH_TRACK and objective.param < len(TTLocalizer.ActionTrackNames):
        data['track'] = TTLocalizer.ActionTrackNames[objective.param]
    try:
        return fmt % data
    except (KeyError, TypeError, ValueError):
        return fmt


class ActionHUD(DirectObject):

    TOAST_LIFETIME = 1.6
    MAX_TOASTS = 5
    BANNER_LIFETIME = 3.0
    # ``FriendsListPanel`` is reparented to a2dTopRight at x=-0.233 and is
    # roughly half a unit wide, so the contract is right-aligned to the left of
    # it instead of underneath it (the two used to overlap).
    CONTRACT_RIGHT_EDGE = -0.52

    def __init__(self, streetName):
        DirectObject.__init__(self)
        self.streetName = streetName
        self.tier = ActionGlobals.DEFAULT_TIER
        # Set while the shared run is detached from its street (shop, tunnel
        # or building).  A RUN_STARTED announce seen during that window is
        # the replacement street's echo of the preserved run, not a fresh
        # start, so the rolling counters must survive it.
        self.runPreserved = False
        self.kills = 0
        self.runStartedAt = globalClock.getFrameTime()
        self.startMoney = getattr(getattr(base, 'localAvatar', None), 'getMoney', lambda: 0)()
        self.objectivesCompleted = 0
        self.dodges = 0
        self.damageTaken = 0
        self.cachesOpened = 0
        self.peakPressure = 0
        self.toasts = []
        self.tracks = []
        self.slotFrames = []
        self.slotTexts = []
        self.slotCooldownBars = []
        self.selectedSlot = 0
        self.loadout = []
        self.slotCooldowns = {}
        self.audio = CombatAudio()
        self.chainUntil = 0.0
        self.targetUntil = 0.0
        self.targetId = None
        self.displayHp = None
        self.lastHealth = None
        self.toastTracks = {}
        self.signFont = ToontownGlobals.getSignFont()
        self.uiFont = ToontownGlobals.getInterfaceFont()
        self._build()
        self._acceptEvents()
        taskMgr.add(self.__updateTask, 'actionHudUpdate')

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build(self):
        self.root = DirectFrame(parent=aspect2d, relief=None, pos=(0, 0, 0))
        self.root.setBin('gui-popup', 40)

        # -- top-left: street / tier / pressure ------------------------
        topLeft = DirectFrame(parent=base.a2dTopLeft, relief=DGG.FLAT,
                             frameSize=(-0.02, 0.98, -0.29, 0.07),
                             frameColor=(0.035, 0.055, 0.09, 0.78), pos=(0.05, 0, -0.08))
        self.topLeft = topLeft
        self.streetText = OnscreenText(parent=topLeft, text=self.streetName.upper(), pos=(0, 0), scale=0.07,
                                       fg=(1, 1, 1, 0.95), shadow=(0, 0, 0, 0.8), align=TextNode.ALeft,
                                       font=self.signFont, mayChange=True)
        self.tierText = OnscreenText(parent=topLeft, text=TTLocalizer.ActionHudTier % self.tier, pos=(0, -0.075),
                                     scale=0.055, fg=(1, 0.85, 0.35, 0.95), shadow=(0, 0, 0, 0.8),
                                     align=TextNode.ALeft, font=self.signFont, mayChange=True)
        self.pressureLabel = OnscreenText(parent=topLeft, text=TTLocalizer.ActionHudPressure, pos=(0, -0.14),
                                          scale=0.038, fg=(0.9, 0.9, 0.9, 0.9), shadow=(0, 0, 0, 0.8),
                                          align=TextNode.ALeft, font=self.uiFont, mayChange=False)
        self.pressureBar = DirectWaitBar(parent=topLeft, relief=DGG.FLAT, range=1.0, value=0.0,
                                         frameSize=(0, 0.62, -0.022, 0.022), pos=(0, 0, -0.185),
                                         frameColor=(0.08, 0.08, 0.1, 0.75), barColor=(0.45, 0.85, 0.55, 0.95),
                                         text='', text_scale=0.03)
        self.stageText = OnscreenText(parent=topLeft, text=TTLocalizer.ActionStageNames[0], pos=(0.64, -0.196),
                                      scale=0.045, fg=(0.45, 0.85, 0.55, 1), shadow=(0, 0, 0, 0.8),
                                      align=TextNode.ALeft, font=self.signFont, mayChange=True)
        self.killText = OnscreenText(parent=topLeft, text=TTLocalizer.ActionHudKills % 0, pos=(0, -0.25),
                                     scale=0.04, fg=(0.9, 0.9, 0.9, 0.9), shadow=(0, 0, 0, 0.8),
                                     align=TextNode.ALeft, font=self.uiFont, mayChange=True)
        self.beanText = OnscreenText(parent=topLeft, text='', pos=(0.53, -0.25),
                                     scale=0.04, fg=(1, 0.85, 0.35, 0.9), shadow=(0, 0, 0, 0.8),
                                     align=TextNode.ALeft, font=self.uiFont, mayChange=True)

        # -- top-right: contract ---------------------------------------
        topRight = DirectFrame(parent=base.a2dTopRight, relief=None,
                               pos=(self.CONTRACT_RIGHT_EDGE, 0, -0.08))
        self.topRight = topRight
        self.contractTitle = OnscreenText(parent=topRight, text=TTLocalizer.ActionHudObjectives, pos=(0, 0),
                                          scale=0.055, fg=(1, 0.85, 0.35, 0.95), shadow=(0, 0, 0, 0.8),
                                          align=TextNode.ARight, font=self.signFont, mayChange=False)
        self.objectiveTexts = []
        self.objectiveBars = []
        for index in range(ActionGlobals.OBJECTIVES_PER_RUN):
            text = OnscreenText(parent=topRight, text='', pos=(0, -0.075 - 0.085 * index), scale=0.035,
                                fg=(0.95, 0.95, 0.95, 0.95), shadow=(0, 0, 0, 0.8), align=TextNode.ARight,
                                font=self.uiFont, mayChange=True)
            self.objectiveTexts.append(text)
            self.objectiveBars.append(DirectWaitBar(
                parent=topRight, relief=DGG.FLAT, range=1.0, value=0.0,
                frameSize=(-0.65, 0, -0.006, 0.006), pos=(0, 0, -0.095 - 0.085 * index),
                frameColor=(0.04, 0.06, 0.1, 0.7), barColor=(0.4, 0.85, 0.65, 0.9), text=''))

        # -- bottom: gag slots -----------------------------------------
        bottom = DirectFrame(parent=base.a2dBottomCenter, relief=None, pos=(0, 0, 0.09))
        self.bottom = bottom
        self.healthBar = DirectWaitBar(parent=bottom, relief=DGG.FLAT, range=1.0, value=1.0,
                                      frameSize=(-0.64, 0.64, -0.012, 0.012), pos=(0, 0, 0.12),
                                      frameColor=(0.04, 0.06, 0.1, 0.85),
                                      barColor=(0.4, 0.9, 0.65, 1), text='')
        self.healthText = OnscreenText(parent=bottom, text='', pos=(0, 0.15), scale=0.04,
                                      fg=(1, 1, 1, 1), shadow=(0, 0, 0, 1),
                                      font=self.uiFont, mayChange=True)
        self.chainText = OnscreenText(parent=bottom, text='', pos=(0, 0.23), scale=0.045,
                                     fg=(1, 0.85, 0.35, 1), shadow=(0, 0, 0, 1),
                                     font=self.signFont, mayChange=True)
        self.chainBar = DirectWaitBar(parent=bottom, relief=DGG.FLAT, range=1.0, value=0.0,
                                     frameSize=(-0.3, 0.3, -0.004, 0.004), pos=(0, 0, 0.21),
                                     frameColor=(0, 0, 0, 0), barColor=(1, 0.8, 0.3, 0.9), text='')
        self.targetText = OnscreenText(parent=self.root, text='', pos=(0, 0.19), scale=0.042,
                                      fg=(1, 1, 1, 1), shadow=(0, 0, 0, 1),
                                      font=self.uiFont, mayChange=True)
        self.targetBar = DirectWaitBar(parent=self.root, relief=DGG.FLAT, range=1.0, value=0.0,
                                      frameSize=(-0.25, 0.25, -0.005, 0.005), pos=(0, 0, 0.165),
                                      frameColor=(0.04, 0.04, 0.06, 0.7),
                                      barColor=(1, 0.65, 0.25, 1), text='')
        self.targetBar.hide()
        slotWidth = 0.42
        for index in range(ActionGlobals.MAX_EQUIPPED_TRACKS):
            x = (index - 1) * (slotWidth + 0.03)
            frame = DirectFrame(parent=bottom, relief=DGG.FLAT, frameSize=(-slotWidth / 2, slotWidth / 2, -0.055, 0.055),
                                frameColor=(0.05, 0.05, 0.08, 0.7), pos=(x, 0, 0))
            keyText = OnscreenText(parent=frame, text=str(index + 1), pos=(-slotWidth / 2 + 0.035, -0.015),
                                   scale=0.045, fg=(1, 1, 1, 0.7), font=self.signFont, mayChange=False)
            nameText = OnscreenText(parent=frame, text=TTLocalizer.ActionSlotEmpty, pos=(0.02, -0.015), scale=0.045,
                                    fg=(1, 1, 1, 0.95), shadow=(0, 0, 0, 0.8), font=self.signFont, mayChange=True)
            cooldown = DirectWaitBar(parent=frame, relief=DGG.FLAT, range=1.0, value=1.0,
                                     frameSize=(-slotWidth / 2 + 0.01, slotWidth / 2 - 0.01, -0.052, -0.04),
                                     frameColor=(0, 0, 0, 0), barColor=(1, 1, 1, 0.6), text='')
            self.slotFrames.append(frame)
            self.slotTexts.append(nameText)
            self.slotCooldownBars.append(cooldown)

        # -- centre: hit marker / vignette / banners -------------------
        self.vignette = DirectFrame(parent=render2d, relief=DGG.FLAT, frameSize=(-1, 1, -1, 1),
                                    frameColor=(0.8, 0.05, 0.05, 0.0))
        self.vignette.setTransparency(TransparencyAttrib.MAlpha)
        self.vignette.setBin('fixed', 5)
        self.vignetteTrack = None

        lines = LineSegs('ActionHitMarker')
        lines.setThickness(2.5)
        lines.setColor(1, 1, 1, 1)
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            lines.moveTo(0.012 * sx, 0, 0.012 * sy)
            lines.drawTo(0.03 * sx, 0, 0.03 * sy)
        self.hitMarker = aspect2d.attachNewNode(lines.create())
        self.hitMarker.setTransparency(TransparencyAttrib.MAlpha)
        self.hitMarker.setBin('fixed', 101)
        self.hitMarker.setColorScale(1, 1, 1, 0)
        self.hitMarkerTrack = None

        self.banner = OnscreenText(parent=aspect2d, text='', pos=(0, 0.42), scale=0.09, fg=(1, 0.9, 0.4, 1),
                                   shadow=(0, 0, 0, 1), align=TextNode.ACenter, font=self.signFont, mayChange=True)
        self.bannerSub = OnscreenText(parent=aspect2d, text='', pos=(0, 0.34), scale=0.05, fg=(1, 1, 1, 1),
                                      shadow=(0, 0, 0, 1), align=TextNode.ACenter, font=self.uiFont, mayChange=True)
        self.banner.setColorScale(1, 1, 1, 0)
        self.bannerSub.setColorScale(1, 1, 1, 0)
        self.bannerTrack = None

        self.toastRoot = DirectFrame(parent=aspect2d, relief=None, pos=(0, 0, -0.3))

        self._refreshBeans()
        self.refreshLoadout()

    def _acceptEvents(self):
        # The friends panel owns the same top-right corner as the street
        # contract.  Do not make two unrelated UI systems fight over it.
        self.accept('openFriendsList', self.__hideContract)
        self.accept('friends-list-shown', self.__hideContract)
        self.accept('friends-list-done', self.__showContract)
        self.accept('action-tier', self.__handleTier)
        self.accept('action-pressure', self.__handlePressure)
        self.accept('action-objectives', self.__handleObjectives)
        self.accept('action-objective-complete', self.__handleObjectiveComplete)
        self.accept('action-announce', self.__handleAnnounce)
        self.accept('action-cog-defeated', self.__handleCogDefeated)
        self.accept('action-gag-hit', self.__handleGagHit)
        self.accept('action-toon-hit', self.__handleToonHit)
        self.accept('action-toon-dodged', self.__handleToonDodged)
        self.accept('action-cache-opened', self.__handleCacheOpened)
        self.accept('action-toon-up', self.__handleToonUp)
        self.accept('action-slot-selected', self.__handleSlotSelected)
        self.accept('action-slot-fired', self.__handleSlotFired)
        self.accept('action-loadout-changed', self.__handleLoadoutChanged)
        if hasattr(base, 'localAvatar'):
            self.accept(base.localAvatar.uniqueName('moneyChange'), self.__handleMoneyChange)

    def __hideContract(self):
        if hasattr(self, 'topRight'):
            self.topRight.hide()

    def __showContract(self):
        if hasattr(self, 'topRight'):
            self.topRight.show()

    def destroy(self):
        taskMgr.remove('actionHudUpdate')
        self.ignoreAll()
        self.audio.destroy()
        for interval in self.toastTracks.values():
            interval.pause()
        self.toastTracks.clear()
        for track in self.tracks:
            track.finish()
        self.tracks = []
        for toast in self.toasts:
            toast.removeNode()
        self.toasts = []
        for track in (self.vignetteTrack, self.hitMarkerTrack, self.bannerTrack):
            if track:
                track.finish()
        self.hitMarker.removeNode()
        self.vignette.destroy()
        self.banner.destroy()
        self.bannerSub.destroy()
        self.toastRoot.destroy()
        self.topLeft.destroy()
        self.topRight.destroy()
        self.bottom.destroy()
        self.root.destroy()

    def _track(self, interval):
        self.tracks = [item for item in self.tracks if not item.isStopped()]
        self.tracks.append(interval)
        interval.start()

    # ------------------------------------------------------------------
    # Tier / pressure / objectives
    # ------------------------------------------------------------------
    def setStreetName(self, name):
        self.streetName = name
        self.streetText.setText(name.upper())

    def __handleTier(self, tier):
        self.tier = tier
        self.tierText.setText(TTLocalizer.ActionHudTier % tier)

    def __handlePressure(self, pressure, stage, oldStage):
        self.peakPressure = max(self.peakPressure, max(0, int(pressure)))
        stage = max(0, min(ActionGlobals.NUM_PRESSURE_STAGES - 1, stage))
        _, fraction = ActionGlobals.getPressureStageFraction(pressure)
        color = ActionGlobals.PRESSURE_STAGE_COLORS[stage]
        self.pressureBar['value'] = fraction
        self.pressureBar['barColor'] = color
        self.stageText.setText(TTLocalizer.ActionStageNames[stage])
        self.stageText.setFg(color)
        if stage != oldStage and stage > 0:
            self.audio.play('warning')
            self.showBanner(TTLocalizer.ActionStageNames[stage], TTLocalizer.ActionStageBanners[stage], color)

    def __handleObjectives(self, objectives):
        for index, text in enumerate(self.objectiveTexts):
            if index >= len(objectives):
                text.setText('')
                self.objectiveBars[index].hide()
                continue
            objective = objectives[index]
            self.objectiveBars[index].show()
            self.objectiveBars[index]['value'] = min(1.0, objective.progress / max(1, objective.target))
            line = describeObjective(objective)
            if objective.complete:
                text.setText('%s  [DONE]' % line)
                text.setFg((0.55, 0.9, 0.55, 0.9))
            else:
                text.setText('%s  %d/%d' % (line, objective.progress, objective.target))
                text.setFg((0.95, 0.95, 0.95, 0.95))

    def __handleObjectiveComplete(self, index, beans, track, xp):
        self.objectivesCompleted += 1
        self.audio.play('complete')
        trackName = TTLocalizer.ActionTrackNames[track] if 0 <= track < len(TTLocalizer.ActionTrackNames) else ''
        self.addToast(TTLocalizer.ActionObjectiveComplete % {'beans': beans, 'xp': xp, 'track': trackName},
                      Vec4(0.6, 1.0, 0.6, 1), scale=0.05)

    def __handleAnnounce(self, kind, avId, value):
        if kind == ActionGlobals.ANNOUNCE_KILL_CHAIN:
            if hasattr(base, 'localAvatar') and avId == base.localAvatar.getDoId():
                self.chainUntil = globalClock.getFrameTime() + ActionGlobals.KILL_CHAIN_WINDOW
                bonus = min(ActionGlobals.KILL_CHAIN_MAX_BONUS,
                            max(0, value - 1) * ActionGlobals.KILL_CHAIN_BONUS_STEP)
                self.chainText.setText(TTLocalizer.ActionChain % (value, int(round(bonus * 100))))
        elif kind == ActionGlobals.ANNOUNCE_BREAKTHROUGH:
            self.audio.play('complete')
            self.showBanner('STREET BREAKTHROUGH', TTLocalizer.ActionBreakthrough % value, Vec4(0.55, 0.95, 1.0, 1))
        elif kind == ActionGlobals.ANNOUNCE_RUN_STARTED:
            if self.runPreserved:
                self.runPreserved = False
                return
            self.chainUntil = 0.0
            self.runStartedAt = globalClock.getFrameTime()
            self.startMoney = getattr(getattr(base, 'localAvatar', None), 'getMoney', lambda: 0)()
            self.objectivesCompleted = self.dodges = self.damageTaken = self.cachesOpened = self.peakPressure = 0
            self.kills = 0
            self.killText.setText(TTLocalizer.ActionHudKills % self.kills)
            self.showBanner(TTLocalizer.ActionRunStarted % value, TTLocalizer.ActionTierHintBanner,
                            Vec4(1, 0.9, 0.4, 1))
        elif kind == ActionGlobals.ANNOUNCE_BUILDING_FLOOR_CLEARED:
            self.audio.play('complete')
            self.showBanner(TTLocalizer.ActionBuildingFloorCleared % value,
                            TTLocalizer.ActionBuildingNextFloorHint, Vec4(0.6, 1.0, 0.6, 1))
        elif kind == ActionGlobals.ANNOUNCE_BUILDING_EXTRACTED:
            self.audio.play('complete')
            self.showBanner(TTLocalizer.ActionBuildingExtractedTitle,
                            TTLocalizer.ActionBuildingExtracted % value, Vec4(0.55, 0.95, 1.0, 1))
        elif kind == ActionGlobals.ANNOUNCE_CONTRACT_CLEARED:
            self.showBanner(TTLocalizer.ActionContractClearedTitle, TTLocalizer.ActionContractCleared % value,
                            Vec4(0.6, 1.0, 0.6, 1))
        elif kind == ActionGlobals.ANNOUNCE_TOON_DOWN:
            if hasattr(base, 'localAvatar') and avId != base.localAvatar.getDoId():
                self.addToast(TTLocalizer.ActionToonDown, Vec4(1, 0.6, 0.6, 1))
        elif kind == ActionGlobals.ANNOUNCE_CACHE_SPAWNED:
            self.addToast(TTLocalizer.ActionCacheSpawned, Vec4(1, 0.95, 0.5, 1), scale=0.045)
        elif kind == ActionGlobals.ANNOUNCE_ELITE_SPAWNED:
            self.addToast(TTLocalizer.ActionEliteSpawned % value, Vec4(0.9, 0.5, 1.0, 1), scale=0.045)

    # ------------------------------------------------------------------
    # Combat feedback
    # ------------------------------------------------------------------
    def __handleCogDefeated(self, suitId, toonId, beans, track, xp, elite, styleName, level):
        if not hasattr(base, 'localAvatar') or toonId != base.localAvatar.getDoId():
            return
        self.kills += 1
        self.audio.play('reward')
        self.killText.setText(TTLocalizer.ActionHudKills % self.kills)
        self.addToast(TTLocalizer.ActionKillToast % beans, Vec4(1, 0.85, 0.35, 1))
        trackName = TTLocalizer.ActionTrackNames[track] if 0 <= track < len(TTLocalizer.ActionTrackNames) else ''
        self.addToast(TTLocalizer.ActionXpToast % (xp, trackName), _trackColor(track), scale=0.04)

    def __handleGagHit(self, suitId, track, level, damage):
        self.flashHitMarker(_trackColor(track))
        self.audio.play('hit')
        self.targetId = suitId
        self.targetUntil = globalClock.getFrameTime() + 3.0

    def __handleToonHit(self, damage, attackId, suitId):
        self.chainUntil = 0.0
        self.damageTaken += max(0, int(damage))
        self.flashVignette(min(0.75, 0.25 + damage * 0.02))
        if hasattr(base, 'localAvatar') and hasattr(base.localAvatar, 'orbitalCamera'):
            base.localAvatar.orbitalCamera.addShake(min(1.0, 0.25 + damage * 0.03))

    def __handleToonDodged(self, attackId, suitId):
        self.dodges += 1
        self.audio.play('dodge')
        self.addToast(TTLocalizer.ActionDodged, Vec4(0.6, 0.9, 1.0, 1), scale=0.045)

    def __handleCacheOpened(self, track, beans):
        self.cachesOpened += 1
        self.audio.play('complete')
        if track >= 0 and track < len(TTLocalizer.ActionTrackNames):
            name = TTLocalizer.ActionTrackNames[track]
            self.showBanner(TTLocalizer.ActionNewTrack, TTLocalizer.ActionNewTrackSub % name, _trackColor(track),
                            lifetime=4.5)
        elif beans > 0:
            self.addToast(TTLocalizer.ActionCacheBeans % beans, Vec4(1, 0.85, 0.35, 1))

    def __handleToonUp(self, avId, level, amount):
        if hasattr(base, 'localAvatar') and avId == base.localAvatar.getDoId() and amount > 0:
            self.audio.play('reward')
            self.addToast(TTLocalizer.ActionToonUpToast % amount, Vec4(0.6, 1.0, 0.6, 1))

    def __handleMoneyChange(self, money):
        self._refreshBeans()

    def _refreshBeans(self):
        if hasattr(base, 'localAvatar'):
            self.beanText.setText(TTLocalizer.ActionHudBeans % base.localAvatar.getMoney())

    def flashHitMarker(self, color):
        if self.hitMarkerTrack:
            self.hitMarkerTrack.finish()
        self.hitMarker.setColorScale(color[0], color[1], color[2], 1)
        self.hitMarker.setScale(1.25)
        self.hitMarkerTrack = Parallel(
            LerpScaleInterval(self.hitMarker, 0.12, 1.0, blendType='easeOut'),
            LerpColorScaleInterval(self.hitMarker, 0.18, Vec4(color[0], color[1], color[2], 0)))
        self.hitMarkerTrack.start()

    def flashVignette(self, strength):
        if self.vignetteTrack:
            self.vignetteTrack.finish()
        self.vignette['frameColor'] = (0.8, 0.05, 0.05, strength)
        self.vignetteTrack = Sequence(
            LerpColorScaleInterval(self.vignette, 0.45, Vec4(1, 1, 1, 0), startColorScale=Vec4(1, 1, 1, 1)),
            Func(self.vignette.setColorScale, 1, 1, 1, 1),
            Func(self.__setVignetteAlpha, 0.0))
        self.vignetteTrack.start()

    def __setVignetteAlpha(self, alpha):
        self.vignette['frameColor'] = (0.8, 0.05, 0.05, alpha)

    def showBanner(self, title, subtitle, color, lifetime=None):
        if self.bannerTrack:
            self.bannerTrack.finish()
        lifetime = lifetime or self.BANNER_LIFETIME
        self.banner.setText(title)
        self.banner.setFg(color)
        self.bannerSub.setText(subtitle or '')
        self.banner.setScale(0.12)
        nodes = (self.banner, self.bannerSub)
        appear = Parallel(*[LerpColorScaleInterval(node, 0.2, Vec4(1, 1, 1, 1), startColorScale=Vec4(1, 1, 1, 0))
                            for node in nodes])
        appear.append(LerpScaleInterval(self.banner, 0.25, 0.09, startScale=0.12, blendType='easeOut'))
        fade = Parallel(*[LerpColorScaleInterval(node, 0.5, Vec4(1, 1, 1, 0)) for node in nodes])
        self.bannerTrack = Sequence(appear, Wait(lifetime), fade)
        self.bannerTrack.start()

    def addToast(self, text, color, scale=0.05):
        toast = OnscreenText(parent=self.toastRoot, text=text, pos=(0, 0), scale=scale,
                             fg=(color[0], color[1], color[2], 1), shadow=(0, 0, 0, 0.9),
                             align=TextNode.ACenter, font=self.signFont, mayChange=False)
        toast.setTransparency(TransparencyAttrib.MAlpha)
        self.toasts = [item for item in self.toasts if not item.isEmpty()]
        # Push older toasts up so they stack.
        for older in self.toasts:
            older.setZ(older.getZ() + 0.06)
        self.toasts.append(toast)
        while len(self.toasts) > self.MAX_TOASTS:
            oldest = self.toasts.pop(0)
            oldTrack = self.toastTracks.pop(id(oldest), None)
            if oldTrack:
                oldTrack.pause()
            oldest.removeNode()
        # Only fade each toast. A position interval would overwrite the stack
        # offsets whenever another reward arrives during its animation.
        interval = Sequence(
            Wait(self.TOAST_LIFETIME * 0.6),
            LerpColorScaleInterval(toast, self.TOAST_LIFETIME * 0.4, Vec4(1, 1, 1, 0)),
            Func(self.__removeToast, toast))
        self.toastTracks[id(toast)] = interval
        interval.start()

    def __removeToast(self, toast):
        self.toastTracks.pop(id(toast), None)
        if toast in self.toasts:
            self.toasts.remove(toast)
        if not toast.isEmpty():
            toast.removeNode()

    # ------------------------------------------------------------------
    # Loadout slots
    # ------------------------------------------------------------------
    def refreshLoadout(self):
        if not hasattr(base, 'localAvatar'):
            return
        toon = base.localAvatar
        self.loadout = ActionProgression.getEquippedTracks(toon)
        for index, text in enumerate(self.slotTexts):
            frame = self.slotFrames[index]
            if index < len(self.loadout):
                track = self.loadout[index]
                tier = ActionProgression.getTrackTier(toon, track)
                roman = TTLocalizer.ActionRoman[min(tier, ActionGlobals.MAX_TRACK_TIER)]
                text.setText('%s %s' % (TTLocalizer.ActionTrackNames[track], roman))
                color = _trackColor(track, 0.75)
                frame['frameColor'] = (color[0] * 0.35, color[1] * 0.35, color[2] * 0.35, 0.75)
            else:
                text.setText(TTLocalizer.ActionSlotEmpty)
                frame['frameColor'] = (0.05, 0.05, 0.08, 0.5)
        self.__highlightSlot()

    def __handleLoadoutChanged(self, tracks=None):
        self.refreshLoadout()

    def __handleSlotSelected(self, slotIndex, track, tier):
        if slotIndex != self.selectedSlot:
            self.audio.play('select')
        self.selectedSlot = slotIndex
        self.__highlightSlot()

    def __highlightSlot(self):
        for index, frame in enumerate(self.slotFrames):
            if index == self.selectedSlot and index < len(self.loadout):
                frame.setScale(1.08)
                self.slotTexts[index].setFg((1, 1, 1, 1))
            else:
                frame.setScale(1.0)
                self.slotTexts[index].setFg((1, 1, 1, 0.75))

    def __handleSlotFired(self, slotIndex, cooldown):
        self.slotCooldowns[slotIndex] = (globalClock.getFrameTime(), max(0.05, cooldown))

    def __updateTask(self, task):
        now = globalClock.getFrameTime()
        self.__updateCombatInfo(now)
        for index, bar in enumerate(self.slotCooldownBars):
            entry = self.slotCooldowns.get(index)
            if entry is None:
                bar['value'] = 1.0
                continue
            started, duration = entry
            fraction = min(1.0, (now - started) / duration)
            bar['value'] = fraction
            if fraction >= 1.0:
                del self.slotCooldowns[index]
                if index == self.selectedSlot:
                    self.audio.play('ready')
        return Task.cont

    def __updateCombatInfo(self, now):
        remaining = max(0.0, self.chainUntil - now)
        self.chainBar['value'] = remaining / ActionGlobals.KILL_CHAIN_WINDOW
        if not remaining:
            self.chainText.setText('')
        toon = getattr(base, 'localAvatar', None)
        if toon is not None:
            hp = max(0, toon.getHp())
            maximum = max(1, toon.getMaxHp())
            if self.displayHp is None:
                self.displayHp = float(hp)
            dt = min(0.1, max(0.0, globalClock.getDt()))
            self.displayHp += (hp - self.displayHp) * (1.0 - 0.001 ** dt)
            fraction = min(1.0, hp / maximum)
            self.healthBar['value'] = min(1.0, self.displayHp / maximum)
            self.healthBar['barColor'] = (1, 0.3, 0.25, 1) if fraction <= 0.3 else (0.4, 0.9, 0.65, 1)
            health = (hp, maximum)
            if health != self.lastHealth:
                self.healthText.setText(TTLocalizer.ActionLaffReadout % health)
                if fraction <= 0.3 and hp > 0:
                    self.audio.play('warning')
                self.lastHealth = health
        repository = getattr(base, 'cr', None)
        target = getattr(repository, 'doId2do', {}).get(self.targetId)
        if target is not None and now < self.targetUntil:
            hp = max(0, getattr(target, 'currHP', 0))
            maximum = max(1, getattr(target, 'maxHP', 1))
            self.targetText.setText('%s  %d / %d' % (target.getName(), hp, maximum))
            self.targetBar['value'] = min(1.0, hp / maximum)
            self.targetBar.show()
        else:
            self.targetText.setText('')
            self.targetBar.hide()

    def getRunSummary(self):
        """Snapshot genuine local feedback for the extraction/game-over panel."""
        money = getattr(getattr(base, 'localAvatar', None), 'getMoney', lambda: self.startMoney)()
        return {
            'kills': self.kills,
            'beans': max(0, int(money) - self.startMoney),
            'objectives': self.objectivesCompleted,
            'dodges': self.dodges,
            'damage': self.damageTaken,
            'caches': self.cachesOpened,
            'pressure': self.peakPressure,
            'seconds': max(0, int(globalClock.getFrameTime() - self.runStartedAt)),
            'tier': self.tier,
        }
