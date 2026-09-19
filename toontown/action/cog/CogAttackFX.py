"""Client presentation for real-time Cog attacks.

The AI decides everything; this module only *shows* it.  Each attack goes
through two calls that mirror the network messages:

``playAttack``    wind-up telegraph + attack animation + projectile flight
``playResolved``  impact (beam flash / pulse ring / splat) and Toon feedback

Every shape gets a readable tell so the player can learn the dodge:

* melee / beam  -> the Cog flashes the telegraph colour while winding up and
                   a beam line is drawn along the locked facing on resolve
* projectile    -> a prop leaves the Cog's hand and flies to the aim point
* aoe           -> a ring on the ground grows during the wind-up and pulses
                   out to the real radius when the attack fires
"""

import math

from panda3d.core import (LineSegs, NodePath, Point3, TransparencyAttrib, Vec3, Vec4)
from direct.interval.IntervalGlobal import (ActorInterval, Func, LerpColorScaleInterval,
                                             LerpScaleInterval, Parallel, ProjectileInterval,
                                             Sequence, SoundInterval, Wait, LerpPosInterval)

from toontown.action import ActionGlobals
from toontown.action.cog import CogAttackRegistry
from toontown.battle import BattleProps, MovieUtil
from toontown.battle.BattleSounds import globalBattleSoundCache
from toontown.battle import SuitBattleGlobals


def _makeRing(radius, color, segments=40, thickness=2.5):
    lines = LineSegs('ActionRing')
    lines.setThickness(thickness)
    lines.setColor(*color)
    for index in range(segments + 1):
        angle = (index / float(segments)) * math.pi * 2.0
        x = math.cos(angle) * radius
        y = math.sin(angle) * radius
        if index == 0:
            lines.moveTo(x, y, 0.05)
        else:
            lines.drawTo(x, y, 0.05)
    node = NodePath(lines.create())
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setLightOff(1)
    node.setDepthWrite(False)
    node.setBin('fixed', 10)
    return node


def _makeBeam(start, end, color, thickness=6.0):
    lines = LineSegs('ActionBeam')
    lines.setThickness(thickness)
    lines.setColor(*color)
    lines.moveTo(start)
    lines.drawTo(end)
    node = render.attachNewNode(lines.create())
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setLightOff(1)
    node.setDepthWrite(False)
    return node


def _makeSector(radius, halfAngle, color):
    """Outline the actual melee/beam cone, with a visible locked centreline."""
    lines = LineSegs('ActionSector')
    lines.setThickness(2.5)
    lines.setColor(*color)
    lines.moveTo(0, 0, 0.08)
    for index in range(33):
        angle = math.radians(-halfAngle + 2.0 * halfAngle * index / 32.0)
        lines.drawTo(math.sin(angle) * radius, math.cos(angle) * radius, 0.08)
    lines.drawTo(0, 0, 0.08)
    lines.drawTo(0, radius, 0.08)
    node = NodePath(lines.create())
    node.setTransparency(TransparencyAttrib.MAlpha)
    node.setLightOff(1)
    node.setDepthWrite(False)
    return node


class CogAttackFX:
    """Per-Cog visual state for real-time attacks."""

    def __init__(self, suit):
        self.suit = suit
        self.tracks = []
        self.telegraphRing = None
        self.deathTrack = None
        self.deathActor = None
        self.animLockedUntil = 0.0
        self.pendingAttacks = {}

    def cleanup(self):
        for track in self.tracks:
            track.finish()
        self.tracks = []
        self._removeRing()
        if self.deathTrack:
            self.deathTrack.finish()
            self.deathTrack = None
        if self.deathActor:
            self.deathActor.cleanup()
            self.deathActor = None
        self.pendingAttacks = {}
        self.suit = None

    def _addTrack(self, track):
        self.tracks = [item for item in self.tracks if not item.isStopped()]
        self.tracks.append(track)
        track.start()
        return track

    def isAnimLocked(self):
        return globalClock.getFrameTime() < self.animLockedUntil

    def _removeRing(self):
        if self.telegraphRing:
            self.telegraphRing.removeNode()
            self.telegraphRing = None

    def _sound(self, name):
        if not name:
            return None
        try:
            return globalBattleSoundCache.getSound(name)
        except Exception:
            return None

    def _animName(self, attackId):
        suit = self.suit
        try:
            attackType = SuitBattleGlobals.SuitAttackType(int(attackId))
            info = SuitBattleGlobals.getSuitAttack(suit.dna.name, suit.getActualLevel(), attackType)
            return info.get('animName', 'magic1')
        except Exception:
            return 'magic1'

    # ------------------------------------------------------------------
    # Attack start
    # ------------------------------------------------------------------
    def playAttack(self, attackId, targetId, aimPoint, windupMs, mutation):
        suit = self.suit
        if suit is None or suit.isEmpty():
            return
        realtime = CogAttackRegistry.getRealtimeAttackById(attackId)
        if realtime is None:
            return
        realtime = CogAttackRegistry.mutate(realtime, mutation)
        windup = max(0.15, windupMs / 1000.0)
        aim = Point3(*aimPoint)
        self.pendingAttacks[attackId] = (realtime, aim)

        animName = self._animName(attackId)
        try:
            animDuration = max(0.5, suit.getDuration(animName))
        except Exception:
            animName = 'magic1'
            animDuration = 1.5
        self.animLockedUntil = globalClock.getFrameTime() + windup + 0.6

        color = Vec4(*realtime.telegraphColor)
        flash = Sequence(
            LerpColorScaleInterval(suit, windup * 0.5, Vec4(color[0], color[1], color[2], 1.0),
                                   startColorScale=Vec4(1, 1, 1, 1), blendType='easeIn'),
            LerpColorScaleInterval(suit, windup * 0.5, Vec4(1, 1, 1, 1), blendType='easeOut'),
            Func(self._clearColorScale))
        anim = Sequence(ActorInterval(suit, animName, startTime=0.0, endTime=min(animDuration, windup + 0.6)))
        parts = [flash, anim]

        sound = self._sound(realtime.sound)
        if sound:
            parts.append(Sequence(Wait(windup * 0.5), SoundInterval(sound, node=suit, volume=0.9)))

        shape = realtime.shape
        if shape == CogAttackRegistry.SHAPE_AOE:
            parts.append(self._aoeTelegraph(realtime, windup))
        elif shape == CogAttackRegistry.SHAPE_PROJECTILE:
            parts.append(Sequence(Wait(windup), Func(self._launchProjectile, realtime, aim)))
        elif shape in (CogAttackRegistry.SHAPE_MELEE, CogAttackRegistry.SHAPE_BEAM):
            parts.append(self._coneTelegraph(realtime, aim, windup, color))
        self._addTrack(Parallel(*parts, name=suit.uniqueName('actionAttack-%d' % attackId)))

    def _clearColorScale(self):
        if self.suit is not None and not self.suit.isEmpty():
            self.suit.clearColorScale()

    def _aoeTelegraph(self, realtime, windup):
        self._removeRing()
        ring = _makeRing(1.0, realtime.telegraphColor)
        ring.reparentTo(render)
        ring.setPos(self.suit.getPos(render))
        ring.setScale(0.5)
        self.telegraphRing = ring
        return Sequence(LerpScaleInterval(ring, windup, realtime.splashRadius, startScale=0.5, blendType='easeIn'))

    def _coneTelegraph(self, realtime, aim, windup, color):
        self._removeRing()
        ring = _makeSector(realtime.maxRange, realtime.coneHalfAngle, color)
        ring.reparentTo(render)
        origin = self.suit.getPos(render)
        ring.setPos(origin)
        delta = aim - origin
        ring.setH(math.degrees(math.atan2(-delta.x, delta.y)))
        self.telegraphRing = ring
        return Sequence(Wait(windup + 0.3), Func(self._removeRing))

    def _groundTelegraph(self, radius, windup, color):
        self._removeRing()
        ring = _makeRing(radius, (color[0], color[1], color[2], 0.6), thickness=1.5)
        ring.reparentTo(self.suit)
        self.telegraphRing = ring
        return Sequence(Wait(windup + 0.3), Func(self._removeRing))

    def _launchProjectile(self, realtime, aim):
        suit = self.suit
        if suit is None or suit.isEmpty():
            return
        propName = realtime.prop or 'paper'
        try:
            prop = BattleProps.globalPropPool.getProp(propName)
        except Exception:
            prop = None
        if prop is None or prop.isEmpty():
            return
        prop.reparentTo(render)
        try:
            startPos = suit.getRightHand().getPos(render)
        except Exception:
            startPos = suit.getPos(render) + Vec3(0, 0, suit.getHeight() * 0.6)
        prop.setPos(startPos)
        prop.setScale(1.0)
        distance = (Point3(aim) - startPos).length()
        duration = max(0.2, distance / max(8.0, realtime.projectileSpeed))
        endPos = Point3(aim.getX(), aim.getY(), aim.getZ() + 1.2)
        flight = Sequence(
            ProjectileInterval(prop, startPos=startPos, endPos=endPos, duration=duration, gravityMult=0.6),
            Func(self._splat, prop, endPos, realtime))
        self._addTrack(flight)

    def _splat(self, prop, pos, realtime):
        if prop is not None and not prop.isEmpty():
            fade = Sequence(
                Parallel(LerpScaleInterval(prop, 0.15, 1.6, blendType='easeOut'),
                         LerpColorScaleInterval(prop, 0.15, Vec4(1, 1, 1, 0))),
                Func(MovieUtil.removeProp, prop))
            prop.setTransparency(TransparencyAttrib.MAlpha)
            self._addTrack(fade)
        if realtime.splashRadius > 0.0:
            self._pulseRing(pos, realtime.splashRadius, realtime.telegraphColor, parent=render)

    def _pulseRing(self, pos, radius, color, parent=None):
        ring = _makeRing(1.0, color, thickness=3.5)
        if parent is None:
            ring.reparentTo(render)
            ring.setPos(self.suit.getPos(render))
        else:
            ring.reparentTo(parent)
            ring.setPos(pos)
        pulse = Sequence(
            Parallel(LerpScaleInterval(ring, 0.35, max(0.5, radius), startScale=0.3, blendType='easeOut'),
                     LerpColorScaleInterval(ring, 0.35, Vec4(1, 1, 1, 0), startColorScale=Vec4(1, 1, 1, 1))),
            Func(ring.removeNode))
        self._addTrack(pulse)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def playResolved(self, attackId, toonId, damage):
        suit = self.suit
        if suit is None or suit.isEmpty():
            return
        pending = self.pendingAttacks.pop(attackId, None)
        realtime = pending[0] if pending else CogAttackRegistry.getRealtimeAttackById(attackId)
        if realtime is None:
            return
        aim = pending[1] if pending else None
        shape = realtime.shape
        color = realtime.telegraphColor
        if shape == CogAttackRegistry.SHAPE_AOE:
            self._removeRing()
            self._pulseRing(None, realtime.splashRadius, color)
        elif shape == CogAttackRegistry.SHAPE_BEAM:
            self._removeRing()
            self._beamFlash(realtime, aim, color)
        elif shape == CogAttackRegistry.SHAPE_MELEE:
            self._removeRing()

        isLocal = hasattr(base, 'localAvatar') and toonId == base.localAvatar.getDoId()
        if isLocal:
            if damage > 0:
                messenger.send('action-toon-hit', [damage, attackId, suit.getDoId()])
            else:
                messenger.send('action-toon-dodged', [attackId, suit.getDoId()])

    def _beamFlash(self, realtime, aim, color):
        suit = self.suit
        try:
            start = suit.getPos(render) + Vec3(0, 0, suit.getHeight() * 0.7)
        except Exception:
            start = suit.getPos(render)
        forward = render.getRelativeVector(suit, Vec3(0, 1, 0))
        forward.normalize()
        end = start + forward * realtime.maxRange
        if aim is not None:
            end = Point3(aim.getX(), aim.getY(), aim.getZ() + 1.5)
        beam = _makeBeam(start, end, color)
        fade = Sequence(LerpColorScaleInterval(beam, 0.28, Vec4(1, 1, 1, 0), startColorScale=Vec4(1, 1, 1, 1)),
                        Func(beam.removeNode))
        self._addTrack(fade)

    # ------------------------------------------------------------------
    # Status / death
    # ------------------------------------------------------------------
    def playStatus(self, status, durationMs):
        suit = self.suit
        if suit is None or suit.isEmpty():
            return
        duration = max(0.2, durationMs / 1000.0)
        if status == ActionGlobals.STATUS_SOAKED:
            tint = Vec4(0.55, 0.75, 1.0, 1.0)
        elif status == ActionGlobals.STATUS_LURED:
            tint = Vec4(0.75, 1.0, 0.7, 1.0)
        elif status == ActionGlobals.STATUS_STAGGER:
            tint = Vec4(1.0, 0.85, 0.55, 1.0)
        else:
            return
        track = Sequence(Func(suit.setColorScale, tint), Wait(duration), Func(self._clearColorScale),
                         name=suit.uniqueName('actionStatus-%d' % status))
        self._addTrack(track)

    def playDeath(self):
        suit = self.suit
        if suit is None or suit.isEmpty() or self.deathTrack is not None:
            return
        self._removeRing()
        for track in self.tracks:
            track.finish()
        self.tracks = []
        try:
            deathSuit = suit.getLoseActor()
        except Exception:
            deathSuit = None
        spinningSound = base.loader.loadSfx('phase_3.5/audio/sfx/Cog_Death.ogg')
        deathSound = base.loader.loadSfx('phase_3.5/audio/sfx/ENC_cogfall_apart.ogg')
        duration = max(1.0, ActionGlobals.COG_DEATH_DELAY - 0.2)
        if deathSuit is not None and not deathSuit.isEmpty():
            self.deathActor = deathSuit
            deathSuit.reparentTo(render)
            deathSuit.setPos(suit.getPos(render))
            deathSuit.setHpr(suit.getHpr(render))
            suit.hide()
            suit.hideNametag3d()
            try:
                loseDuration = deathSuit.getDuration('lose')
            except Exception:
                loseDuration = duration
            animTrack = Sequence(
                ActorInterval(deathSuit, 'lose', startTime=0.0, endTime=min(loseDuration, duration)),
                Func(deathSuit.hide))
            explosionTrack = Sequence(
                Wait(duration - 0.6),
                MovieUtil.createKapowExplosionTrack(deathSuit, explosionPoint=Point3(0, 0, suit.getHeight() * 0.5),
                                                    scale=1.4))
            soundTrack = Sequence(Wait(0.2), SoundInterval(spinningSound, node=deathSuit, duration=duration - 0.7),
                                  SoundInterval(deathSound, node=deathSuit))
            self.deathTrack = Parallel(animTrack, explosionTrack, soundTrack, name=suit.uniqueName('actionDeath'))
        else:
            self.deathTrack = Sequence(
                Parallel(LerpColorScaleInterval(suit, duration * 0.6, Vec4(1, 1, 1, 0)),
                         SoundInterval(deathSound, node=suit)),
                Func(suit.hide), name=suit.uniqueName('actionDeath'))
        self.deathTrack.start()
