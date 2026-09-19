"""First-person gag weapons driven by the equipped three-track loadout.

This is deliberately client-side presentation only.  Every hit is sent to the
target Cog's distributed object (or to the street planner for traps and
toon-ups); the AI validates equipped tracks, tiers, range and cooldowns and
owns the damage.

Each equipped track is a weapon slot (keys 1/2/3).  The gag inside the slot
is the highest tier the Toon owns for that track and its behaviour comes from
:data:`ActionGlobals.GAG_DEFS`:

* PROJECTILE  arcing prop at the crosshair target (Throw)
* STREAM      hitscan spray / blast (Squirt)
* RADIAL      every Cog in a cone / radius around the Toon (Sound)
* DROP        falls from the sky onto the aim point after a delay (Drop)
* LURE        status effect on the target (Lure)
* TRAP        deployable hazard placed on the ground (Trap)
* HEAL        self toon-up (Toon-Up)
"""

import math

from panda3d.core import (BitMask32, CollisionHandlerQueue, CollisionNode, CollisionRay,
                          CollisionSegment, CollisionTraverser, LineSegs, NodePath, Point3,
                          TransparencyAttrib, Vec3, Vec4)
from direct.interval.IntervalGlobal import (Func, LerpColorScaleInterval, LerpHprInterval,
                                             LerpPosHprInterval, LerpPosInterval, LerpScaleInterval,
                                             Parallel, ProjectileInterval, Sequence, Wait)
from direct.showbase.DirectObject import DirectObject
from direct.task import Task

from toontown.action import ActionGlobals, ActionProgression
from toontown.battle import BattleProps, MovieUtil
from toontown.battle.BattleSounds import globalBattleSoundCache
from toontown.suit import DistributedSuitBase
from toontown.toonbase import ToontownGlobals

# Props that are not in the battle prop pool.
PROP_MODEL_PATHS = {
    'cupcake': 'phase_6/models/golf/picnic_cupcake.bam',
}
# Fallback chains for pool names that differ between Toontown forks.
PROP_FALLBACKS = {
    'fruitpie': ('fruitpie', 'creampie', 'fruitpie-slice'),
    'birthday-cake': ('birthday-cake', 'wedding-cake', 'creampie'),
    'bottle': ('bottle', 'glass', 'squirting-flower'),
    'firehose': ('firehose', 'water-gun', 'bottle'),
    'fog_horn': ('fog_horn', 'elephant', 'bugle'),
    'hypno-goggles': ('hypno-goggles', 'big-magnet'),
}
VIEWMODEL_SCALE = {
    'cupcake': 0.42, 'fruitpie': 0.3, 'birthday-cake': 0.2,
    'squirting-flower': 0.34, 'bottle': 0.3, 'firehose': 0.22,
    'bikehorn': 0.3, 'aoogah': 0.28, 'fog_horn': 0.2,
    'flowerpot': 0.22, 'anvil': 0.14, 'piano': 0.06,
    '1dollar': 0.32, 'big-magnet': 0.16, 'hypno-goggles': 0.3,
    'banana': 0.3, 'trapdoor': 0.08, 'tnt': 0.22,
    'feather': 0.3, 'megaphone': 0.22, 'lipstick': 0.3,
}
DEFAULT_VIEWMODEL_SCALE = 0.3
IDLE_POS = Point3(0.48, 1.15, -0.38)
IDLE_HPR = Vec3(18.0, -12.0, 5.0)
FIRE_POS = Point3(0.36, 0.88, -0.18)
FIRE_HPR = Vec3(-10.0, -24.0, -8.0)
LOWERED_POS = Point3(0.48, 1.05, -0.9)


def _trackColor(track, alpha=0.9):
    r, g, b = ActionGlobals.TRACK_COLORS[track]
    return Vec4(r, g, b, alpha)


class GagViewModel(DirectObject):
    """A camera-attached, three-slot first-person weapon rig."""

    AUTOFIRE_STYLES = (ActionGlobals.GAG_STYLE_PROJECTILE, ActionGlobals.GAG_STYLE_STREAM,
                       ActionGlobals.GAG_STYLE_RADIAL)

    def __init__(self, camera, subject):
        DirectObject.__init__(self)
        self.camera = camera
        self.subject = subject
        self.root = camera.attachNewNode('FirstPersonGagViewModel')
        self.root.setLightOff(1)
        self.loadout = []
        self.slot = 0
        self.prop = None
        self.propName = None
        self.fireTrack = None
        self.swapTrack = None
        self.fxTracks = []
        self.lastFireTime = {}
        self.triggerHeld = False
        self.visible = False
        self.triggerTaskName = 'gagViewModelTrigger'
        self.accept('action-loadout-changed', self.__handleLoadoutChanged)
        self.accept('action-track-access-changed', self.__handleLoadoutChanged)
        self.refreshLoadout()

    def destroy(self):
        self.ignoreAll()
        taskMgr.remove(self.triggerTaskName)
        self._finishTrack()
        if self.swapTrack:
            self.swapTrack.finish()
            self.swapTrack = None
        for track in self.fxTracks:
            track.finish()
        self.fxTracks = []
        if self.prop:
            self._removeProp(self.prop)
            self.prop = None
        if not self.root.isEmpty():
            self.root.removeNode()
        self.camera = None
        self.subject = None

    # ------------------------------------------------------------------
    # Loadout / selection
    # ------------------------------------------------------------------
    def refreshLoadout(self):
        if self.subject is None:
            return
        self.loadout = ActionProgression.getEquippedTracks(self.subject)
        if self.slot >= len(self.loadout):
            self.slot = 0
        self._showSlotProp()
        self._announceSelection()

    def __handleLoadoutChanged(self, *args):
        self.refreshLoadout()

    def getCurrentTrack(self):
        if self.slot < len(self.loadout):
            return self.loadout[self.slot]
        return None

    def getCurrentGagDef(self):
        track = self.getCurrentTrack()
        if track is None:
            return None
        level = ActionProgression.getMaxLevelForTrack(self.subject, track)
        return ActionGlobals.getGagDef(track, level)

    def select(self, slotIndex):
        if slotIndex < 0 or slotIndex >= ActionGlobals.MAX_EQUIPPED_TRACKS:
            return
        if slotIndex >= len(self.loadout) or slotIndex == self.slot:
            self.slot = min(slotIndex, max(0, len(self.loadout) - 1))
            self._announceSelection()
            return
        self.slot = slotIndex
        self._finishTrack()
        self._swapProp()
        self._announceSelection()

    def cycle(self, delta):
        if not self.loadout:
            return
        self.select((self.slot + delta) % len(self.loadout))

    def _announceSelection(self):
        track = self.getCurrentTrack()
        tier = ActionProgression.getTrackTier(self.subject, track) if track is not None else 0
        messenger.send('action-slot-selected', [self.slot, track if track is not None else -1, tier])

    # ------------------------------------------------------------------
    # Props
    # ------------------------------------------------------------------
    def _loadProp(self, name):
        path = PROP_MODEL_PATHS.get(name)
        if path:
            try:
                return loader.loadModel(path)
            except Exception:
                return None
        for candidate in PROP_FALLBACKS.get(name, (name,)):
            try:
                prop = BattleProps.globalPropPool.getProp(candidate)
            except Exception:
                prop = None
            if prop is not None and not prop.isEmpty():
                return prop
        return None

    def _removeProp(self, prop):
        if prop is None or prop.isEmpty():
            return
        MovieUtil.removeProp(prop)

    def _showSlotProp(self):
        gagDef = self.getCurrentGagDef()
        wanted = gagDef.prop if gagDef else None
        if wanted == self.propName and self.prop is not None:
            return
        if self.prop:
            self._removeProp(self.prop)
            self.prop = None
        self.propName = wanted
        if wanted is None:
            return
        prop = self._loadProp(wanted)
        if prop is None:
            return
        prop.reparentTo(self.root)
        prop.setName('ViewModel-%s' % wanted)
        prop.setTwoSided(True)
        prop.setPosHpr(IDLE_POS, IDLE_HPR)
        prop.setScale(VIEWMODEL_SCALE.get(wanted, DEFAULT_VIEWMODEL_SCALE))
        prop.setTransparency(TransparencyAttrib.MAlpha)
        self.prop = prop
        self.setVisible(self.visible)

    def _swapProp(self):
        if self.swapTrack:
            self.swapTrack.finish()
        if self.prop is None:
            self._showSlotProp()
            return
        oldProp = self.prop
        self.prop = None
        self.propName = None
        self.swapTrack = Sequence(
            LerpPosInterval(oldProp, 0.1, LOWERED_POS, blendType='easeIn'),
            Func(self._removeProp, oldProp),
            Func(self._showSlotProp),
            Func(self._raiseProp),
            name='GagViewModelSwap')
        self.swapTrack.start()

    def _raiseProp(self):
        if self.prop is None:
            return
        self.prop.setPos(LOWERED_POS)
        raise_ = LerpPosInterval(self.prop, 0.14, IDLE_POS, blendType='easeOut')
        self._addFx(raise_)

    def setVisible(self, visible):
        self.visible = bool(visible)
        if self.visible:
            self.root.show()
        else:
            self.root.hide()
            self.setTrigger(False)

    # ------------------------------------------------------------------
    # Trigger handling
    # ------------------------------------------------------------------
    def setTrigger(self, held):
        held = bool(held)
        if held == self.triggerHeld:
            return
        self.triggerHeld = held
        taskMgr.remove(self.triggerTaskName)
        if held:
            self.fire()
            taskMgr.add(self.__triggerTask, self.triggerTaskName)

    def __triggerTask(self, task):
        if not self.triggerHeld or self.subject is None:
            return Task.done
        gagDef = self.getCurrentGagDef()
        if gagDef is not None and gagDef.style in self.AUTOFIRE_STYLES:
            self.fire()
        return Task.cont

    def fire(self):
        if not self.visible or self.subject is None or self.camera is None:
            return False
        if getattr(self.subject, 'hp', 1) <= 0:
            return False
        gagDef = self.getCurrentGagDef()
        if gagDef is None:
            return False
        now = globalClock.getFrameTime()
        if now - self.lastFireTime.get(gagDef.track, -100.0) < gagDef.cooldown:
            return False
        if self.fireTrack and not self.fireTrack.isStopped():
            return False

        fired = self._fireStyle(gagDef)
        if not fired:
            return False
        self.lastFireTime[gagDef.track] = now
        messenger.send('action-slot-fired', [self.slot, gagDef.cooldown])
        return True

    def _fireStyle(self, gagDef):
        style = gagDef.style
        if style == ActionGlobals.GAG_STYLE_PROJECTILE:
            return self._fireProjectile(gagDef)
        if style == ActionGlobals.GAG_STYLE_STREAM:
            return self._fireStream(gagDef)
        if style == ActionGlobals.GAG_STYLE_RADIAL:
            return self._fireRadial(gagDef)
        if style == ActionGlobals.GAG_STYLE_DROP:
            return self._fireDrop(gagDef)
        if style == ActionGlobals.GAG_STYLE_LURE:
            return self._fireLure(gagDef)
        if style == ActionGlobals.GAG_STYLE_TRAP:
            return self._fireTrap(gagDef)
        if style == ActionGlobals.GAG_STYLE_HEAL:
            return self._fireHeal(gagDef)
        return False

    # ------------------------------------------------------------------
    # Shared presentation helpers
    # ------------------------------------------------------------------
    def _finishTrack(self):
        if self.fireTrack:
            self.fireTrack.finish()
            self.fireTrack = None

    def _addFx(self, interval):
        self.fxTracks = [item for item in self.fxTracks if not item.isStopped()]
        self.fxTracks.append(interval)
        interval.start()
        return interval

    def _playSound(self, name, node=None):
        if not name:
            return
        sfx = globalBattleSoundCache.getSound(name)
        if sfx:
            base.playSfx(sfx, node=node or self.subject)

    def _recoil(self, holdTime=0.07, returnTime=0.18):
        prop = self.prop
        if prop is None or prop.isEmpty():
            return Sequence(Wait(holdTime))
        return Sequence(
            LerpPosHprInterval(prop, 0.1, FIRE_POS, FIRE_HPR, blendType='easeIn'),
            Wait(holdTime),
            LerpPosHprInterval(prop, returnTime, IDLE_POS, IDLE_HPR, blendType='easeOut'))

    def _startFireTrack(self, track):
        self._finishTrack()
        self.fireTrack = track
        self.fireTrack.start()

    def _aimVector(self):
        vector = render.getRelativeVector(self.camera, Vec3(0, 1, 0))
        vector.normalize()
        return vector

    def _cameraPos(self):
        return self.camera.getPos(render)

    def _sendHit(self, target, gagDef):
        if target is None or not hasattr(target, 'sendUpdate'):
            return False
        if target.getHP() <= 0 or getattr(target, 'actionDead', False):
            return False
        target.sendUpdate('requestFreeGagHit', [gagDef.track, gagDef.level])
        return True

    def _liveCogs(self):
        for obj in list(base.cr.doId2do.values()):
            if not isinstance(obj, DistributedSuitBase.DistributedSuitBase):
                continue
            if obj.isEmpty() or obj.getHP() <= 0 or getattr(obj, 'actionDead', False):
                continue
            yield obj

    def _cogsNear(self, point, radius, exclude=None):
        result = []
        for cog in self._liveCogs():
            if exclude is not None and cog is exclude:
                continue
            if (cog.getPos(render) - point).length() <= radius + 1.0:
                result.append(cog)
        return result

    def _findTarget(self, maxRange):
        origin = self._cameraPos()
        end = origin + self._aimVector() * maxRange
        segment = CollisionSegment(origin[0], origin[1], origin[2], end[0], end[1], end[2])
        node = CollisionNode('FirstPersonGagAim')
        node.addSolid(segment)
        node.setFromCollideMask(ToontownGlobals.PieBitmask)
        node.setIntoCollideMask(BitMask32.allOff())
        collider = render.attachNewNode(node)
        queue = CollisionHandlerQueue()
        traverser = CollisionTraverser('FirstPersonGagAim')
        traverser.addCollider(collider, queue)
        traverser.traverse(render)
        queue.sortEntries()
        try:
            for index in range(queue.getNumEntries()):
                entry = queue.getEntry(index)
                target = self._targetFromEntry(entry)
                if target is None or target is self.subject:
                    continue
                if target.getHP() <= 0 or getattr(target, 'actionDead', False):
                    continue
                point = entry.getSurfacePoint(render) if entry.hasSurfacePoint() else target.getPos(render)
                return target, point
        finally:
            collider.removeNode()

        # Some reduced Cog models do not expose the normal avatar collision
        # solid even though they are valid action targets.  Keep aim usable by
        # accepting a Cog whose body is actually close to the fired ray.  The
        # server still validates range, ownership, cooldown and damage.
        best = None
        bestDistance = None
        aimVector = self._aimVector()
        for cog in self._liveCogs():
            distance, along = self._distanceToCogRay(cog, origin, end, aimVector)
            if distance is None or along < 0.0 or along > maxRange:
                continue
            if bestDistance is None or distance < bestDistance:
                best = cog
                bestDistance = distance
        if best is not None:
            return best, Point3(best.getPos(render))
        return None, end

    @staticmethod
    def _distanceToSegment(point, start, end):
        segment = Vec3(end - start)
        lengthSquared = segment.lengthSquared()
        if lengthSquared <= 1e-6:
            return (point - start).length()
        t = max(0.0, min(1.0, Vec3(point - start).dot(segment) / lengthSquared))
        return (point - (start + segment * t)).length()

    @classmethod
    def _distanceToCogRay(cls, cog, start, end, aimVector):
        """Return (distance, projected distance) to a Cog's visible bounds.

        The normal PieBitmask solid is intentionally used first, but some
        Cog variants only expose a small or missing gag collision solid.  A
        center-point test is also wrong for first-person aiming because the
        Cog's network position is at its feet while the crosshair is usually
        on its torso or head.  Use the rendered bounds as a soft client-side
        aim assist; the AI still performs the authoritative range checks.
        """
        center = Point3(cog.getPos(render))
        radius = 1.5
        try:
            bounds = cog.getTightBounds(render)
            if bounds and len(bounds) == 2 and not bounds[0].isNan() and not bounds[1].isNan():
                center = Point3((bounds[0] + bounds[1]) * 0.5)
                radius = max(1.0, (bounds[1] - center).length())
        except Exception:
            try:
                center.setZ(center.getZ() + cog.getHeight() * 0.5)
                radius = max(radius, cog.getRadius() + 0.75)
            except Exception:
                pass

        # Do not let a very large model create a map-wide hit cone.
        radius = min(radius + 0.35, 3.5)
        distance = cls._distanceToSegment(center, start, end)
        along = (center - start).dot(aimVector)
        if distance > radius:
            return None, along
        return distance, along

    def _findFloorPoint(self, maxRange):
        """Where the crosshair meets the world (floor or wall) within range."""
        origin = self._cameraPos()
        end = origin + self._aimVector() * maxRange
        segment = CollisionSegment(origin[0], origin[1], origin[2], end[0], end[1], end[2])
        node = CollisionNode('FirstPersonGagFloorAim')
        node.addSolid(segment)
        node.setFromCollideMask(ToontownGlobals.FloorBitmask | ToontownGlobals.WallBitmask)
        node.setIntoCollideMask(BitMask32.allOff())
        collider = render.attachNewNode(node)
        queue = CollisionHandlerQueue()
        traverser = CollisionTraverser('FirstPersonGagFloorAim')
        traverser.addCollider(collider, queue)
        traverser.traverse(render)
        queue.sortEntries()
        try:
            for index in range(queue.getNumEntries()):
                entry = queue.getEntry(index)
                if entry.hasSurfacePoint():
                    return Point3(entry.getSurfacePoint(render))
        finally:
            collider.removeNode()
        # Nothing hit: drop the point onto the Toon's floor height.
        point = Point3(end)
        point.setZ(self.subject.getPos(render).getZ())
        return point

    def _targetFromEntry(self, entry):
        nodePath = entry.getIntoNodePath()
        while not nodePath.isEmpty():
            name = nodePath.getName()
            if name.startswith('distAvatarCollNode-'):
                try:
                    doId = int(name.rsplit('-', 1)[1])
                except ValueError:
                    return None
                target = getattr(getattr(base, 'cr', None), 'doId2do', {}).get(doId)
                if target is not None and hasattr(target, 'getStyleName') and hasattr(target, 'getHP'):
                    return target
                return None
            nodePath = nodePath.getParent()
        return None

    def _groundRing(self, point, radius, color, duration=0.4):
        lines = LineSegs('GagRing')
        lines.setThickness(3.0)
        lines.setColor(*color)
        segments = 36
        for index in range(segments + 1):
            angle = (index / float(segments)) * math.pi * 2.0
            x = math.cos(angle)
            y = math.sin(angle)
            if index == 0:
                lines.moveTo(x, y, 0.05)
            else:
                lines.drawTo(x, y, 0.05)
        ring = render.attachNewNode(lines.create())
        ring.setTransparency(TransparencyAttrib.MAlpha)
        ring.setLightOff(1)
        ring.setDepthWrite(False)
        ring.setPos(point)
        ring.setScale(0.3)
        self._addFx(Sequence(
            Parallel(LerpScaleInterval(ring, duration, max(0.5, radius), blendType='easeOut'),
                     LerpColorScaleInterval(ring, duration, Vec4(1, 1, 1, 0), startColorScale=Vec4(1, 1, 1, 1))),
            Func(ring.removeNode)))

    def _splatAt(self, point, color, scale=1.0):
        try:
            splat = BattleProps.globalPropPool.getProp('splat')
        except Exception:
            splat = None
        if splat is None or splat.isEmpty():
            return
        splat.reparentTo(render)
        splat.setPos(point)
        splat.setScale(0.01)
        splat.setColor(color)
        splat.setBillboardPointEye()
        self._addFx(Sequence(
            LerpScaleInterval(splat, 0.15, 1.2 * scale, blendType='easeOut'),
            Wait(0.25),
            LerpColorScaleInterval(splat, 0.2, Vec4(1, 1, 1, 0)),
            Func(MovieUtil.removeProp, splat)))

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------
    def _fireProjectile(self, gagDef):
        target, targetPoint = self._findTarget(gagDef.range)
        startPos = self._cameraPos()
        endPos = targetPoint if target is not None else startPos + self._aimVector() * min(gagDef.range, 42.0)
        prop = self.prop
        color = _trackColor(gagDef.track)

        def launch():
            if prop is None or prop.isEmpty():
                return
            prop.hide()
            projectile = prop.copyTo(render)
            projectile.show()
            projectile.setPos(render, startPos)
            projectile.setScale(render, prop.getScale())
            projectile.setHpr(render, self.camera.getHpr(render))
            distance = max(1.0, (Point3(endPos) - startPos).length())
            duration = max(0.18, distance / max(10.0, gagDef.projectileSpeed))
            flight = Sequence(
                ProjectileInterval(projectile, startPos=startPos, endPos=endPos, duration=duration, gravityMult=0.55),
                Func(self._finishProjectile, projectile, target, Point3(endPos), gagDef, color))
            self._addFx(flight)

        self._playSound(gagDef.sound)
        self._startFireTrack(Sequence(
            Func(prop.show) if prop is not None else Wait(0.0),
            LerpPosHprInterval(prop, 0.11, FIRE_POS, FIRE_HPR, blendType='easeIn') if prop is not None else Wait(0.11),
            Wait(0.05),
            Func(launch),
            LerpPosHprInterval(prop, 0.18, IDLE_POS, IDLE_HPR, blendType='easeOut') if prop is not None else Wait(0.18),
            Func(self._restoreProp),
            name='FirstPersonThrow'))
        return True

    def _restoreProp(self):
        if self.prop is not None and not self.prop.isEmpty():
            self.prop.show()

    def _finishProjectile(self, projectile, target, point, gagDef, color):
        if projectile is not None and not projectile.isEmpty():
            projectile.removeNode()
        self._playSound(gagDef.hitSound or gagDef.sound, node=target)
        self._splatAt(point, color, scale=1.0 + gagDef.splash * 0.25)
        hitAny = self._sendHit(target, gagDef)
        if gagDef.splash > 0.0:
            for cog in self._cogsNear(point, gagDef.splash, exclude=target):
                self._sendHit(cog, gagDef)
            self._groundRing(point, gagDef.splash, color)
        return hitAny

    def _fireStream(self, gagDef):
        target, targetPoint = self._findTarget(gagDef.range)
        origin = self._cameraPos()
        endPos = targetPoint if target is not None else origin + self._aimVector() * gagDef.range
        color = _trackColor(gagDef.track)

        def spray():
            stream = LineSegs('FirstPersonSpray')
            stream.setThickness(0.06 + 0.05 * gagDef.tier)
            stream.setColor(0.55, 0.82, 1.0, 0.9)
            stream.moveTo(origin + Vec3(0.3, 0, -0.25))
            stream.drawTo(endPos)
            streamNode = render.attachNewNode(stream.create())
            streamNode.setTransparency(TransparencyAttrib.MAlpha)
            streamNode.setLightOff(1)
            self._addFx(Sequence(
                LerpColorScaleInterval(streamNode, 0.14 + 0.06 * gagDef.tier, Vec4(1, 1, 1, 0),
                                       startColorScale=Vec4(1, 1, 1, 1)),
                Func(streamNode.removeNode)))
            if target is not None:
                self._sendHit(target, gagDef)
                self._splatAt(Point3(endPos), Vec4(0.55, 0.82, 1.0, 1.0), scale=0.8 + 0.3 * gagDef.tier)
                if gagDef.splash > 0.0:
                    for cog in self._cogsNear(Point3(endPos), gagDef.splash, exclude=target):
                        self._sendHit(cog, gagDef)

        self._playSound(gagDef.sound)
        self._startFireTrack(Sequence(
            LerpPosHprInterval(self.prop, 0.08, FIRE_POS, FIRE_HPR, blendType='easeIn') if self.prop else Wait(0.08),
            Func(spray),
            Wait(0.06),
            LerpPosHprInterval(self.prop, 0.16, IDLE_POS, IDLE_HPR, blendType='easeOut') if self.prop else Wait(0.16),
            name='FirstPersonSquirt'))
        return True

    def _fireRadial(self, gagDef):
        origin = self.subject.getPos(render)
        forward = self._aimVector()
        forward.setZ(0)
        if forward.lengthSquared() > 1e-4:
            forward.normalize()
        halfAngle = gagDef.cone if gagDef.cone > 0 else 180.0
        color = _trackColor(gagDef.track)
        hits = []
        for cog in self._liveCogs():
            delta = cog.getPos(render) - origin
            flat = Vec3(delta.getX(), delta.getY(), 0)
            distance = flat.length()
            if distance > gagDef.range:
                continue
            if halfAngle < 180.0 and distance > 1e-3:
                flat.normalize()
                cosine = max(-1.0, min(1.0, flat.dot(forward)))
                if math.degrees(math.acos(cosine)) > halfAngle:
                    continue
            hits.append(cog)
        self._playSound(gagDef.sound)
        self._groundRing(origin, gagDef.range, color, duration=0.45)
        for cog in hits:
            self._sendHit(cog, gagDef)
        self._startFireTrack(Sequence(self._recoil(holdTime=0.12, returnTime=0.22), name='FirstPersonSound'))
        return True

    def _fireDrop(self, gagDef):
        target, targetPoint = self._findTarget(gagDef.range)
        if target is not None:
            point = Point3(target.getPos(render))
        else:
            point = self._findFloorPoint(gagDef.range)
        color = _trackColor(gagDef.track)
        propName = gagDef.prop

        def land():
            self._playSound(gagDef.sound)
            self._groundRing(point, gagDef.splash, color, duration=0.35)
            for cog in self._cogsNear(point, gagDef.splash):
                self._sendHit(cog, gagDef)

        def drop():
            prop = self._loadProp(propName)
            if prop is None:
                land()
                return
            prop.reparentTo(render)
            prop.setPos(point + Vec3(0, 0, 14.0))
            prop.setScale(1.0)
            self._addFx(Sequence(
                LerpPosInterval(prop, 0.35, point, blendType='easeIn'),
                Func(land),
                Wait(0.6),
                LerpColorScaleInterval(prop, 0.25, Vec4(1, 1, 1, 0)),
                Func(MovieUtil.removeProp, prop)))

        # Telegraph the landing zone so the player can read their own aim.
        self._groundRing(point, gagDef.splash, Vec4(color[0], color[1], color[2], 0.6), duration=gagDef.dropDelay)
        self._addFx(Sequence(Wait(gagDef.dropDelay), Func(drop)))
        self._startFireTrack(Sequence(self._recoil(), name='FirstPersonDrop'))
        return True

    def _fireLure(self, gagDef):
        target, targetPoint = self._findTarget(gagDef.range)
        color = _trackColor(gagDef.track)
        if target is None and gagDef.splash <= 0.0:
            return False
        centre = Point3(target.getPos(render)) if target is not None else Point3(targetPoint)
        self._playSound(gagDef.sound)
        sent = self._sendHit(target, gagDef) if target is not None else False
        if gagDef.splash > 0.0:
            for cog in self._cogsNear(centre, gagDef.splash, exclude=target):
                if self._sendHit(cog, gagDef):
                    sent = True
            self._groundRing(centre, gagDef.splash, color, duration=0.5)
        if not sent:
            return False
        self._startFireTrack(Sequence(self._recoil(holdTime=0.15, returnTime=0.25), name='FirstPersonLure'))
        return True

    def _fireTrap(self, gagDef):
        planner = getattr(base.cr, 'currSuitPlanner', None)
        if planner is None:
            return False
        point = self._findFloorPoint(gagDef.range)
        if (point - self.subject.getPos(render)).length() > gagDef.range:
            return False
        planner.d_requestActionTrap(gagDef.level, point)
        self._groundRing(point, gagDef.splash, _trackColor(gagDef.track), duration=0.5)
        self._startFireTrack(Sequence(self._recoil(holdTime=0.1, returnTime=0.2), name='FirstPersonTrap'))
        return True

    def _fireHeal(self, gagDef):
        planner = getattr(base.cr, 'currSuitPlanner', None)
        toon = self.subject
        if planner is None or toon.hp >= toon.maxHp:
            return False
        planner.d_requestActionToonUp(gagDef.level)
        self._startFireTrack(Sequence(self._recoil(holdTime=0.2, returnTime=0.3), name='FirstPersonToonUp'))
        return True
