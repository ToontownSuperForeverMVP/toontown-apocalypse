"""Real-time combat brain for a street Cog (AI side).

One controller is attached to every :class:`DistributedSuitAI` spawned by a
suit planner.  The :class:`StreetDirectorAI` ticks all controllers at 10 Hz.

State machine::

    PATROL ── sees / is hit by Toon ──► ALERT ──► ENGAGE ◄──┐
                                                   │  ▲     │
                                        heavy hit  │  │     │ lure ends
                                                   ▼  │     │
                                                STAGGER ───┘   LURED
    ENGAGE ── target gone 8s ──► DEPARTING (flies away)
    any    ── HP <= 0 ──────────► DEFEATED

While patrolling, the Cog keeps following the deterministic DNA path that the
client already animates; the controller only mirrors that position onto the
AI node so range checks work.  Once engaged the AI owns the position and
streams it to clients through the DistributedSmoothNode fields.

Attacks are telegraphed: the client is told the attack, the aim point and the
wind-up before anything is resolved.  Resolution happens on the AI when the
wind-up (plus projectile travel) ends, against the Toon's *current* position,
so moving out of the way genuinely dodges the attack.
"""

import math
import random

from panda3d.core import Point3, Vec3
from panda3d.toontown import DNASuitPoint, SuitLeg
from direct.directnotify import DirectNotifyGlobal

from toontown.action import ActionGlobals
from toontown.action.cog import CogAttackRegistry
from toontown.battle import SuitBattleGlobals

_ENGAGEABLE_LEG_TYPES = (SuitLeg.TWalk, SuitLeg.TWalkToStreet, SuitLeg.TWalkFromStreet)


def _flat(vec):
    return Vec3(vec[0], vec[1], 0.0)


def _headingTo(fromPos, toPos):
    dx = toPos[0] - fromPos[0]
    dy = toPos[1] - fromPos[1]
    if abs(dx) < 1e-5 and abs(dy) < 1e-5:
        return None
    return math.degrees(math.atan2(-dx, dy))


def _angleDelta(a, b):
    delta = (a - b + 180.0) % 360.0 - 180.0
    return abs(delta)


def _distancePointToSegment(point, a, b):
    ab = Vec3(b - a)
    length2 = ab.lengthSquared()
    if length2 <= 1e-6:
        return (Vec3(point - a)).length()
    t = max(0.0, min(1.0, Vec3(point - a).dot(ab) / length2))
    closest = a + ab * t
    return (Vec3(point - closest)).length()


class CogCombatControllerAI:
    notify = DirectNotifyGlobal.directNotify.newCategory('CogCombatControllerAI')

    NAV_REPLAN_INTERVAL = 1.5
    NAV_REPLAN_DISTANCE = 8.0
    WAYPOINT_REACH_DISTANCE = 1.6
    SEPARATION_DISTANCE = 3.2
    LURED_SPEED_FRACTION = 0.35
    SECOND_PULSE_DELAY = 0.45

    def __init__(self, suit, director):
        self.suit = suit
        self.director = director
        self.rng = random.Random()
        attributes = SuitBattleGlobals.getSuitAttributes(suit.dna.name)
        self.attributes = attributes
        self.role = ActionGlobals.getCogRole(suit.dna.name, attributes.tier)
        self.tuning = ActionGlobals.ROLE_TUNING[self.role]
        self.state = ActionGlobals.COG_PATROL
        self.alive = True
        self.targetId = 0
        self.lostTargetSince = None
        self.pos = Point3(0, 0, 0)
        self.heading = 0.0
        self.havePos = False
        self.mutation = -1
        self.attackSet = []
        self.currentAttack = None
        self.nextAttackTime = 0.0
        self.recoveryUntil = 0.0
        self.recoveryMoveAllowed = False
        self.stateUntil = 0.0
        self.statuses = {}
        self.strafeDir = self.rng.choice((-1.0, 1.0))
        self.strafeUntil = 0.0
        self.strafing = False
        self.navPath = []
        self.navTargetPos = None
        self.navPlannedAt = -100.0
        self.lastSendTime = -100.0
        self.lastSentPos = Point3(0, 0, 0)
        self.lastSentHeading = 0.0
        self.stopped = True
        self.elite = bool(getattr(suit, 'actionElite', False))

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def isEngaged(self):
        return self.alive and self.state in (ActionGlobals.COG_ALERT, ActionGlobals.COG_ENGAGE,
                                             ActionGlobals.COG_STAGGER, ActionGlobals.COG_LURED)

    def isActive(self):
        return self.alive and self.state not in (ActionGlobals.COG_DEFEATED, ActionGlobals.COG_DEPARTING)

    def getPos(self):
        return Point3(self.pos)

    def getTargetId(self):
        return self.targetId

    def hasStatus(self, status, now):
        until = self.statuses.get(status)
        return until is not None and until > now

    def isLured(self, now):
        return self.hasStatus(ActionGlobals.STATUS_LURED, now)

    def isSoaked(self, now):
        return self.hasStatus(ActionGlobals.STATUS_SOAKED, now)

    def getActualLevel(self):
        return self.suit.getActualLevel()

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------
    def tick(self, now, dt, profile):
        if not self.isActive():
            return
        self._refreshAttackSet(profile.attackMutation)
        self._expireStatuses(now)
        state = self.state
        if state == ActionGlobals.COG_PATROL:
            self._tickPatrol(now, profile)
        elif state == ActionGlobals.COG_ALERT:
            self._faceTarget()
            if now >= self.stateUntil:
                self._enterEngage(now)
        elif state == ActionGlobals.COG_ENGAGE:
            self._tickEngage(now, dt, profile)
        elif state == ActionGlobals.COG_STAGGER:
            if now >= self.stateUntil:
                self._enterEngage(now)
        elif state == ActionGlobals.COG_LURED:
            self._tickLured(now, dt, profile)
        self._flushPosition(now)

    def _refreshAttackSet(self, mutation):
        if mutation != self.mutation:
            self.mutation = mutation
            self.attackSet = CogAttackRegistry.buildAttackSet(self.attributes, mutation)

    def _expireStatuses(self, now):
        for status in list(self.statuses.keys()):
            if self.statuses[status] <= now:
                del self.statuses[status]

    # ------------------------------------------------------------------
    # PATROL
    # ------------------------------------------------------------------
    def _tickPatrol(self, now, profile):
        pos = self.suit.getCurrentPathPos()
        if pos is None:
            return
        self.pos = Point3(pos)
        self.havePos = True
        self.suit.setPos(self.pos)
        if not self.director.isRunActive():
            return
        if getattr(self.suit, 'legType', None) not in _ENGAGEABLE_LEG_TYPES:
            return
        detectRange = profile.detectRange
        best = None
        bestDist = None
        for toon in self.director.getEngageableToons():
            distance = (_flat(toon.getPos()) - _flat(self.pos)).length()
            if distance > detectRange:
                continue
            if bestDist is None or distance < bestDist:
                best, bestDist = toon, distance
        if best is not None and self.director.requestEngageSlot(best.doId, self):
            self._acquire(best.doId, now)

    def _acquire(self, toonId, now, skipAlert=False):
        self.targetId = toonId
        self.lostTargetSince = None
        if not self.havePos:
            pos = self.suit.getCurrentPathPos()
            if pos is not None:
                self.pos = Point3(pos)
                self.havePos = True
        self.suit.beginActionControl()
        self._faceTarget()
        if skipAlert:
            self._enterEngage(now)
        else:
            self.state = ActionGlobals.COG_ALERT
            self.stateUntil = now + ActionGlobals.COG_ALERT_DURATION
            self.suit.b_setActionState(ActionGlobals.COG_ALERT)
        self._flushPosition(now, force=True)

    def aggro(self, toon, now=None):
        """Immediately engage a Toon when a room is opened.

        Street Cogs normally acquire targets from their patrol detection loop.
        Building rooms are different: every Cog is already present when the
        elevator doors open, so waiting for a patrol tick makes the first
        attack feel random and lets the player walk past the room.  Keep the
        transition in the controller so the same movement/attack rules are
        used by streets and interiors.
        """
        if not self.alive or toon is None:
            return
        if now is None:
            now = globalClock.getFrameTime()
        if self.director.requestEngageSlot(toon.doId, self, force=True):
            self._acquire(toon.doId, now, skipAlert=True)

    def _enterEngage(self, now):
        if not self.alive:
            return
        self.state = ActionGlobals.COG_ENGAGE
        self.suit.b_setActionState(ActionGlobals.COG_ENGAGE)
        self.nextAttackTime = max(self.nextAttackTime, now + 0.4)

    # ------------------------------------------------------------------
    # ENGAGE
    # ------------------------------------------------------------------
    def _getTarget(self):
        if not self.targetId:
            return None
        toon = self.director.getActiveToon(self.targetId)
        if toon is None or getattr(toon, 'hp', 0) <= 0:
            return None
        return toon

    def _retarget(self, now, profile):
        best = None
        bestDist = None
        for toon in self.director.getEngageableToons():
            distance = (_flat(toon.getPos()) - _flat(self.pos)).length()
            if distance > profile.detectRange * 1.5:
                continue
            if bestDist is None or distance < bestDist:
                best, bestDist = toon, distance
        if best is not None and self.director.requestEngageSlot(best.doId, self, force=True):
            self.targetId = best.doId
            self.lostTargetSince = None
            return True
        return False

    def _tickEngage(self, now, dt, profile):
        toon = self._getTarget()
        if toon is None:
            if self.lostTargetSince is None:
                self.lostTargetSince = now
                self.currentAttack = None
                self.director.releaseEngageSlot(self)
                self.targetId = 0
            if self._retarget(now, profile):
                return
            if now - self.lostTargetSince > ActionGlobals.COG_LOST_TARGET_TIMEOUT:
                self.depart()
            return

        toonPos = Point3(toon.getPos())
        distance = (_flat(toonPos) - _flat(self.pos)).length()

        if self.currentAttack is not None:
            self._tickAttack(now, toon, profile)
            if self.currentAttack is not None and not self.currentAttack['moveAllowed']:
                # Telegraphs promise a locked direction, including while the
                # player strafes during wind-up.
                self.heading = self.currentAttack['facing']
                return
        elif now < self.recoveryUntil and not self.recoveryMoveAllowed:
            self._faceTarget()
            return

        self._move(now, dt, profile, toonPos, distance)

        if (self.currentAttack is None and now >= self.nextAttackTime and now >= self.recoveryUntil
                and self.director.canAttackToon(toon.doId, now)):
            pick = CogAttackRegistry.chooseAttack(self.attackSet, distance, self.rng)
            if pick is not None:
                self._startAttack(pick, toon, now, profile, distance)

    def _move(self, now, dt, profile, toonPos, distance):
        speed = ActionGlobals.getCogSpeed(self.role, profile)
        step = speed * dt
        desired = self.tuning.preferredRange
        moveVec = None

        if distance > ActionGlobals.COG_NAV_DIRECT_DISTANCE:
            waypoint = self._nextWaypoint(now, toonPos)
            if waypoint is not None:
                moveVec = _flat(waypoint) - _flat(self.pos)
            else:
                moveVec = _flat(toonPos) - _flat(self.pos)
            self.strafing = False
        elif distance > desired + 2.0:
            self.navPath = []
            moveVec = _flat(toonPos) - _flat(self.pos)
            self.strafing = False
        elif self.tuning.keepDistance and distance < desired - 3.0:
            moveVec = _flat(self.pos) - _flat(toonPos)
            self.strafing = False
        else:
            if now >= self.strafeUntil:
                self.strafeUntil = now + self.rng.uniform(1.2, 2.8)
                self.strafing = self.rng.random() < self.tuning.strafe * min(1.5, profile.aggression)
                if self.rng.random() < 0.5:
                    self.strafeDir *= -1.0
            if self.strafing:
                toToon = _flat(toonPos) - _flat(self.pos)
                if toToon.lengthSquared() > 1e-4:
                    toToon.normalize()
                    moveVec = Vec3(-toToon.y, toToon.x, 0.0) * self.strafeDir
                    step *= 0.7

        if moveVec is not None and moveVec.lengthSquared() > 1e-4:
            length = moveVec.length()
            moveVec.normalize()
            moveVec *= min(step, length)
            moveVec += self._separation(step)
            self.pos = Point3(self.pos + moveVec)

        # Keep the Cog roughly on the Toon's floor height when close enough
        # that it matters; otherwise ease toward the waypoint height.
        if distance < 25.0:
            self.pos.setZ(self.pos.getZ() + (toonPos.getZ() - self.pos.getZ()) * min(1.0, dt * 3.0))
        elif self.navPath:
            self.pos.setZ(self.pos.getZ() + (self.navPath[0].getZ() - self.pos.getZ()) * min(1.0, dt * 3.0))

        heading = _headingTo(self.pos, toonPos)
        if heading is not None:
            self.heading = heading

    def _separation(self, step):
        push = Vec3(0, 0, 0)
        for other in self.director.getEngagedControllers():
            if other is self:
                continue
            delta = _flat(self.pos) - _flat(other.pos)
            distance = delta.length()
            if 1e-4 < distance < self.SEPARATION_DISTANCE:
                delta.normalize()
                push += delta * (self.SEPARATION_DISTANCE - distance) * 0.5
        if push.lengthSquared() > step * step:
            push.normalize()
            push *= step
        return push

    def _faceTarget(self):
        toon = self._getTarget()
        if toon is None:
            return
        heading = _headingTo(self.pos, toon.getPos())
        if heading is not None:
            self.heading = heading

    # ------------------------------------------------------------------
    # Navigation over the DNA suit-point graph
    # ------------------------------------------------------------------
    def _nearestStreetPoint(self, pos):
        planner = self.suit.sp
        if planner is None:
            return None
        best = None
        bestDist = None
        for point in planner.streetPointList:
            distance = (_flat(point.getPos()) - _flat(pos)).lengthSquared()
            if bestDist is None or distance < bestDist:
                best, bestDist = point, distance
        return best

    def _planNav(self, now, toonPos):
        self.navPlannedAt = now
        self.navTargetPos = Point3(toonPos)
        self.navPath = []
        planner = self.suit.sp
        if planner is None:
            return
        start = self._nearestStreetPoint(self.pos)
        end = self._nearestStreetPoint(toonPos)
        if start is None or end is None:
            return
        if start.getIndex() == end.getIndex():
            self.navPath = [Point3(end.getPos())]
            return
        try:
            path = planner.genPath(start, end, 1, 80)
        except Exception:
            path = None
        if not path:
            return
        points = []
        for index in range(path.getNumPoints()):
            point = planner.pointIndexes.get(path.getPointIndex(index))
            if point is None or point.getPointType() != DNASuitPoint.STREETPOINT:
                continue
            points.append(Point3(point.getPos()))
        # Drop a first waypoint that is behind us.
        if len(points) > 1 and (_flat(points[0]) - _flat(self.pos)).length() < 3.0:
            points.pop(0)
        self.navPath = points

    def _nextWaypoint(self, now, toonPos):
        needReplan = (not self.navPath or self.navTargetPos is None
                      or (_flat(toonPos) - _flat(self.navTargetPos)).length() > self.NAV_REPLAN_DISTANCE)
        if needReplan and now - self.navPlannedAt >= self.NAV_REPLAN_INTERVAL:
            self._planNav(now, toonPos)
        while self.navPath and (_flat(self.navPath[0]) - _flat(self.pos)).length() <= self.WAYPOINT_REACH_DISTANCE:
            self.navPath.pop(0)
        if self.navPath:
            return self.navPath[0]
        return None

    # ------------------------------------------------------------------
    # Attacks
    # ------------------------------------------------------------------
    def _startAttack(self, pick, toon, now, profile, distance):
        realtime, attribute = pick
        toonPos = Point3(toon.getPos())
        velocity = self.director.getToonVelocity(toon.doId)
        travel = realtime.travelTime(distance)
        lead = velocity * ((realtime.windup + travel) * realtime.tracking)
        aim = Point3(toonPos + lead)
        heading = _headingTo(self.pos, aim)
        if heading is not None:
            self.heading = heading
        baseDamage = attribute.getBaseAttackDamage(self.getActualLevel())
        damage = max(1, int(round(baseDamage * realtime.damageMult * profile.damageScale)))
        self.currentAttack = {
            'rt': realtime,
            'attr': attribute,
            'aim': aim,
            'startTime': now,
            'resolveTime': now + realtime.windup + travel,
            'pulsesLeft': realtime.pulses,
            'facing': self.heading,
            'moveAllowed': False,
            'damage': damage,
            'targetId': toon.doId,
        }
        self.suit.sendUpdate('actionAttack', [realtime.attackId, toon.doId, aim[0], aim[1], aim[2],
                                              int(realtime.windup * 1000), self.mutation])
        self.director.noteAttackStart(toon.doId, now)

    def _tickAttack(self, now, toon, profile):
        attack = self.currentAttack
        if attack is None or now < attack['resolveTime']:
            return
        realtime = attack['rt']
        hit = self._resolveHit(attack, toon)
        damage = 0
        if hit and getattr(toon, 'hp', 0) > 0:
            damage = attack['damage']
            toon.takeDamage(damage)
        self.suit.sendUpdate('actionAttackResolved', [realtime.attackId, toon.doId, damage])
        self.director.onAttackResolved(self, toon, realtime, damage)

        attack['pulsesLeft'] -= 1
        if attack['pulsesLeft'] > 0 and getattr(toon, 'hp', 0) > 0:
            # Second pulse / double tap: re-aim at where the Toon is now and
            # tell the client so it can show the follow-up.
            toonPos = Point3(toon.getPos())
            attack['aim'] = toonPos
            heading = _headingTo(self.pos, toonPos)
            if heading is not None:
                attack['facing'] = heading
                self.heading = heading
            attack['resolveTime'] = now + self.SECOND_PULSE_DELAY + realtime.travelTime(
                (_flat(toonPos) - _flat(self.pos)).length())
            self.suit.sendUpdate('actionAttack', [realtime.attackId, toon.doId, toonPos[0], toonPos[1],
                                                  toonPos[2], int(self.SECOND_PULSE_DELAY * 1000),
                                                  self.mutation])
            return
        self.currentAttack = None
        self.recoveryUntil = now + realtime.recovery
        self.recoveryMoveAllowed = realtime.moveDuringRecovery
        self.nextAttackTime = now + realtime.cooldown / max(0.5, profile.aggression)

    def _resolveHit(self, attack, toon):
        realtime = attack['rt']
        toonPos = Point3(toon.getPos())
        cogPos = Point3(self.pos)
        radius = ActionGlobals.TOON_HIT_RADIUS
        shape = realtime.shape
        if shape == CogAttackRegistry.SHAPE_MELEE:
            if (toonPos - cogPos).length() > realtime.maxRange + radius:
                return False
            heading = _headingTo(cogPos, toonPos)
            return heading is None or _angleDelta(heading, attack['facing']) <= realtime.coneHalfAngle
        if shape == CogAttackRegistry.SHAPE_BEAM:
            if (toonPos - cogPos).length() > realtime.maxRange + radius:
                return False
            heading = _headingTo(cogPos, toonPos)
            return heading is None or _angleDelta(heading, attack['facing']) <= realtime.coneHalfAngle
        if shape == CogAttackRegistry.SHAPE_AOE:
            # Ground pulses can be jumped over.
            if toonPos.getZ() - cogPos.getZ() > 2.5:
                return False
            return (_flat(toonPos) - _flat(cogPos)).length() <= realtime.splashRadius + radius * 0.5
        # Projectile: the Toon has to be close to the line of travel at impact.
        aim = Point3(attack['aim'])
        direction = Vec3(aim - cogPos)
        if direction.lengthSquared() > 1e-4:
            direction.normalize()
            aim = Point3(aim + direction * 3.0)
        origin = Point3(cogPos.getX(), cogPos.getY(), cogPos.getZ() + 2.5)
        distance = _distancePointToSegment(Point3(toonPos.getX(), toonPos.getY(), toonPos.getZ() + 1.5), origin, aim)
        if distance <= realtime.hitRadius + radius * 0.5:
            return True
        if realtime.splashRadius > 0.0:
            return (_flat(toonPos) - _flat(attack['aim'])).length() <= realtime.splashRadius
        return False

    # ------------------------------------------------------------------
    # LURED
    # ------------------------------------------------------------------
    def _tickLured(self, now, dt, profile):
        toon = self._getTarget()
        if toon is not None:
            toonPos = Point3(toon.getPos())
            delta = _flat(toonPos) - _flat(self.pos)
            distance = delta.length()
            if distance > 3.0:
                delta.normalize()
                step = ActionGlobals.getCogSpeed(self.role, profile) * self.LURED_SPEED_FRACTION * dt
                self.pos = Point3(self.pos + delta * min(step, distance - 3.0))
                self.pos.setZ(self.pos.getZ() + (toonPos.getZ() - self.pos.getZ()) * min(1.0, dt * 3.0))
            heading = _headingTo(self.pos, toonPos)
            if heading is not None:
                self.heading = heading
        if not self.isLured(now):
            self._enterEngage(now)

    # ------------------------------------------------------------------
    # Reactions to the Toon
    # ------------------------------------------------------------------
    def onDamaged(self, toon, gagDef, damage, now):
        if not self.alive:
            return
        if self.state == ActionGlobals.COG_PATROL:
            if self.director.requestEngageSlot(toon.doId, self, force=True):
                self._acquire(toon.doId, now, skipAlert=True)
        elif not self.targetId:
            if self.director.requestEngageSlot(toon.doId, self, force=True):
                self.targetId = toon.doId
                self.lostTargetSince = None
        if gagDef is not None and gagDef.knockback > 0.0 and self.isEngaged():
            away = _flat(self.pos) - _flat(toon.getPos())
            if away.lengthSquared() > 1e-4:
                away.normalize()
                self.pos = Point3(self.pos + away * gagDef.knockback)
        if gagDef is not None and gagDef.track == ActionGlobals.SQUIRT_TRACK:
            self.applyStatus(ActionGlobals.STATUS_SOAKED, ActionGlobals.SOAKED_DURATION, now)
        staggerThreshold = self.suit.maxHP * ActionGlobals.COG_STAGGER_FRACTION * self.tuning.staggerResist
        heavyTrack = gagDef is not None and gagDef.track in (ActionGlobals.DROP_TRACK, ActionGlobals.TRAP_TRACK)
        if self.isEngaged() and (damage >= staggerThreshold or heavyTrack):
            duration = ActionGlobals.TRAP_STAGGER_DURATION if gagDef is not None and gagDef.track == ActionGlobals.TRAP_TRACK \
                else ActionGlobals.COG_STAGGER_DURATION
            self.applyStagger(duration, now)

    def applyStagger(self, duration, now):
        if not self.alive or self.state in (ActionGlobals.COG_LURED,):
            return
        self.currentAttack = None
        self.state = ActionGlobals.COG_STAGGER
        self.stateUntil = now + duration
        self.statuses[ActionGlobals.STATUS_STAGGER] = now + duration
        self.suit.b_setActionState(ActionGlobals.COG_STAGGER)
        self.suit.sendUpdate('actionStatus', [ActionGlobals.STATUS_STAGGER, int(duration * 1000)])

    def applyStatus(self, status, duration, now):
        self.statuses[status] = now + duration
        self.suit.sendUpdate('actionStatus', [status, int(duration * 1000)])

    def applyLure(self, toon, gagDef, now):
        if not self.alive:
            return
        if self.state == ActionGlobals.COG_PATROL or not self.targetId:
            if self.director.requestEngageSlot(toon.doId, self, force=True):
                self._acquire(toon.doId, now, skipAlert=True)
        self.currentAttack = None
        duration = gagDef.duration if gagDef is not None else 4.0
        self.statuses[ActionGlobals.STATUS_LURED] = now + duration
        self.state = ActionGlobals.COG_LURED
        self.suit.b_setActionState(ActionGlobals.COG_LURED)
        self.suit.sendUpdate('actionStatus', [ActionGlobals.STATUS_LURED, int(duration * 1000)])

    def onDefeated(self):
        if not self.alive:
            return
        self.alive = False
        self.currentAttack = None
        self.state = ActionGlobals.COG_DEFEATED
        self.director.releaseEngageSlot(self)
        self.targetId = 0
        self.suit.b_setActionState(ActionGlobals.COG_DEFEATED)

    def depart(self):
        if not self.isActive():
            return
        self.currentAttack = None
        self.state = ActionGlobals.COG_DEPARTING
        self.director.releaseEngageSlot(self)
        self.targetId = 0
        self.suit.b_setActionState(ActionGlobals.COG_DEPARTING)
        self.suit.flyAwayNow()

    def cleanup(self):
        self.alive = False
        self.currentAttack = None
        self.director = None
        self.suit = None

    # ------------------------------------------------------------------
    # Position streaming
    # ------------------------------------------------------------------
    def _flushPosition(self, now, force=False):
        if self.state in (ActionGlobals.COG_PATROL, ActionGlobals.COG_DEFEATED, ActionGlobals.COG_DEPARTING):
            return
        if not force and now - self.lastSendTime < ActionGlobals.COG_POSITION_SEND_INTERVAL:
            return
        self.suit.setPos(self.pos)
        self.suit.setH(self.heading)
        moved = ((self.pos - self.lastSentPos).length() > 0.02
                 or _angleDelta(self.heading, self.lastSentHeading) > 1.0)
        if moved or force:
            self.suit.d_setSmPosHpr(self.pos.getX(), self.pos.getY(), self.pos.getZ(), self.heading, 0.0, 0.0)
            self.lastSentPos = Point3(self.pos)
            self.lastSentHeading = self.heading
            self.lastSendTime = now
            self.stopped = False
        elif not self.stopped:
            self.suit.d_setSmStop()
            self.stopped = True
            self.lastSendTime = now
