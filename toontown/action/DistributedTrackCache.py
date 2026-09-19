"""Gag Training Cache (client side).

A conspicuous spinning safe with a floating label.  Walking into it asks the
AI to open it; the AI answers with the discovered track so the HUD can make a
ceremony out of it ("NEW TRACK DISCOVERED - SOUND").
"""

from panda3d.core import NodePath, Point3, TextNode, TransparencyAttrib, Vec4
from direct.interval.IntervalGlobal import (Func, LerpColorScaleInterval, LerpHprInterval, LerpPosInterval,
                                             Parallel, Sequence)

from toontown.action import ActionGlobals
from toontown.battle import BattleProps
from toontown.safezone import DistributedTreasure
from toontown.toonbase import TTLocalizer, ToontownGlobals


class DistributedTrackCache(DistributedTreasure.DistributedTreasure):

    def __init__(self, cr):
        DistributedTreasure.DistributedTreasure.__init__(self, cr)
        self.grabSoundPath = 'phase_4/audio/sfx/SZ_DD_treasure.ogg'
        self.modelPath = 'action-cache'
        self.scale = 1.0
        self.shadow = 1
        self.fly = 1
        self.zOffset = 0.0
        self.spinTrack = None
        self.label = None

    def delete(self):
        if self.spinTrack:
            self.spinTrack.finish()
            self.spinTrack = None
        if self.label:
            self.label.removeNode()
            self.label = None
        DistributedTreasure.DistributedTreasure.delete(self)

    def getSphereRadius(self):
        return ActionGlobals.CACHE_GRAB_RADIUS

    def prepareModel(self, modelPath, modelFindString):
        root = NodePath('trackCache')
        try:
            prop = BattleProps.globalPropPool.getProp('safe')
            prop.reparentTo(root)
            prop.setScale(0.32)
            prop.setPos(0, 0, 0.2)
        except Exception:
            fallback = loader.loadModel('phase_4/models/props/icecream.bam')
            fallback.reparentTo(root)
        root.setColorScale(1.0, 0.92, 0.55, 1.0)

        text = TextNode('trackCacheLabel')
        text.setFont(ToontownGlobals.getSignFont())
        text.setAlign(TextNode.ACenter)
        text.setTextColor(1.0, 0.95, 0.4, 1.0)
        text.setShadow(0.05, 0.05)
        text.setShadowColor(0, 0, 0, 1)
        text.setText(TTLocalizer.ActionCacheLabel)
        self.label = root.attachNewNode(text.generate())
        self.label.setScale(0.9)
        self.label.setPos(0, 0, 4.2)
        self.label.setBillboardPointEye()
        self.label.setTransparency(TransparencyAttrib.MAlpha)
        return root

    def startAnimation(self):
        if self.spinTrack:
            self.spinTrack.finish()
        spin = LerpHprInterval(self.treasure, 4.0, (360, 0, 0), startHpr=(0, 0, 0))
        bob = Sequence(LerpPosInterval(self.treasure, 1.2, Point3(0, 0, 0.6), startPos=Point3(0, 0, 0),
                                       blendType='easeInOut'),
                       LerpPosInterval(self.treasure, 1.2, Point3(0, 0, 0), startPos=Point3(0, 0, 0.6),
                                       blendType='easeInOut'))
        glow = Sequence(LerpColorScaleInterval(self.treasure, 1.0, Vec4(1.0, 1.0, 0.85, 1.0),
                                               startColorScale=Vec4(1.0, 0.92, 0.55, 1.0)),
                        LerpColorScaleInterval(self.treasure, 1.0, Vec4(1.0, 0.92, 0.55, 1.0),
                                               startColorScale=Vec4(1.0, 1.0, 0.85, 1.0)))
        self.spinTrack = Parallel(spin, bob, glow, name=self.uniqueName('trackCacheSpin'))
        self.spinTrack.loop()

    def handleGrab(self, avId):
        if self.spinTrack:
            self.spinTrack.finish()
            self.spinTrack = None
        if self.label:
            self.label.hide()
        DistributedTreasure.DistributedTreasure.handleGrab(self, avId)

    def setTrackReward(self, track, beans):
        """Only the grabbing Toon receives this."""
        messenger.send('action-cache-opened', [track, beans])
