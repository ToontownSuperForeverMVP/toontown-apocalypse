"""Source/Orange Box style ground and air movement for Toontown.

The walker deliberately keeps Panda3D's GravityWalker collision and lifter
machinery.  Only horizontal acceleration is replaced: gravity, floor contact,
wall pushing, event spheres, and the existing avatar-control task contract all
remain compatible with the rest of Toontown.
"""

import math

from panda3d.core import ClockObject, Point3, Vec3
from direct.showbase.InputStateGlobal import inputState
from direct.showbase.MessengerGlobal import messenger
from direct.task import Task
from direct.task.TaskManagerGlobal import taskMgr
from direct.controls.GravityWalker import GravityWalker


class SourceMovementWalker(GravityWalker):
    """A small, deterministic Quake/Orange Box movement model.

    This is intentionally not a perfect recreation of a particular Source
    branch.  It follows the useful gameplay rules: acceleration instead of
    instant speed, friction only on the ground, air acceleration, preserved
    momentum, and Orange Box-style air control.  Holding jump is an explicit
    auto-jump convenience; it is separate from the air acceleration model so
    backhopping can still build speed without turning this into a surf script.
    """

    GroundAcceleration = 55.0
    AirAcceleration = 18.0
    GroundFriction = 7.0
    StopSpeed = 5.0
    MaxGroundSpeed = 10.0
    MaxAirWishSpeed = 10.0
    BackHopAcceleration = 24.0
    BackHopWishSpeed = 18.0

    def __init__(self, getMovementYaw=None, getMovementBasis=None,
                 setBodyHeading=None,
                 setCrouched=None,
                 gravity=64.348, legacyLifter=False):
        GravityWalker.__init__(self, gravity=gravity,
                               hardLandingForce=16.0,
                               legacyLifter=legacyLifter)
        self.getMovementYaw = getMovementYaw
        self.getMovementBasis = getMovementBasis
        self.setBodyHeading = setBodyHeading
        self.setCrouched = setCrouched
        self.horizontalVelocity = Vec3(0.0, 0.0, 0.0)
        self._crouchWasDown = False

    def reset(self):
        GravityWalker.reset(self)
        self.horizontalVelocity = Vec3(0.0, 0.0, 0.0)
        self._crouchWasDown = False

    def setWalkSpeed(self, forward, jump, reverse, rotate):
        GravityWalker.setWalkSpeed(self, forward, jump, reverse, rotate)
        self.MaxGroundSpeed = float(forward)
        self.MaxAirWishSpeed = float(forward)

    def _applyFriction(self, dt):
        speed = self.horizontalVelocity.length()
        if speed <= 0.0001:
            self.horizontalVelocity = Vec3(0.0, 0.0, 0.0)
            return
        control = max(speed, self.StopSpeed)
        drop = control * self.GroundFriction * dt
        newSpeed = max(speed - drop, 0.0)
        self.horizontalVelocity *= newSpeed / speed

    def _accelerate(self, wishDirection, wishSpeed, acceleration, dt):
        currentSpeed = self.horizontalVelocity.dot(wishDirection)
        addSpeed = wishSpeed - currentSpeed
        if addSpeed <= 0.0:
            return
        accelerationSpeed = min(acceleration * dt * wishSpeed, addSpeed)
        self.horizontalVelocity += wishDirection * accelerationSpeed

    def _movementWish(self):
        forwardInput = float(inputState.isSet("forward"))
        reverseInput = float(inputState.isSet("reverse"))
        forward = forwardInput - reverseInput
        strafe = float(inputState.isSet("slideRight")) - float(inputState.isSet("slideLeft"))
        wish = Vec3(strafe, forward, 0.0)
        magnitude = wish.length()
        if magnitude <= 0.0001:
            return Vec3(0.0, 0.0, 0.0), 0.0, forwardInput, reverseInput
        if magnitude > 1.0:
            wish /= magnitude

        if self.getMovementBasis:
            forwardAxis, rightAxis = self.getMovementBasis()
            forwardAxis = Vec3(forwardAxis.x, forwardAxis.y, 0.0)
            rightAxis = Vec3(rightAxis.x, rightAxis.y, 0.0)
            if forwardAxis.lengthSquared() <= 0.0001 or rightAxis.lengthSquared() <= 0.0001:
                return Vec3(0.0, 0.0, 0.0), 0.0, forwardInput, reverseInput
            forwardAxis.normalize()
            rightAxis.normalize()
        else:
            yaw = self.getMovementYaw() if self.getMovementYaw else self.avatarNodePath.getH()
            radians = math.radians(yaw)
            forwardAxis = Vec3(-math.sin(radians), math.cos(radians), 0.0)
            rightAxis = Vec3(math.cos(radians), math.sin(radians), 0.0)
        worldWish = forwardAxis * wish.y + rightAxis * wish.x
        worldWish.normalize()
        return worldWish, min(magnitude, 1.0), forwardInput, reverseInput

    def _updateBodyHeading(self, wishDirection, wishMagnitude):
        if not self.setBodyHeading or wishMagnitude <= 0.0001:
            return
        heading = math.degrees(math.atan2(-wishDirection.x, wishDirection.y))
        self.setBodyHeading(heading)

    def handleAvatarControls(self, task):
        dt = max(0.0, min(ClockObject.getGlobalClock().getDt(), 0.1))
        grounded = self.lifter.isOnGround()
        jumpDown = bool(inputState.isSet("jump"))
        crouchDown = bool(inputState.isSet("crouch"))

        if self.setCrouched and crouchDown != self._crouchWasDown:
            self.setCrouched(crouchDown)
        self._crouchWasDown = crouchDown

        if grounded:
            if self.isAirborne:
                self.isAirborne = 0
                impact = self.lifter.getImpactVelocity()
                # Holding jump is an explicit auto-hop request.  Bypass the
                # legacy landing gate so the next grounded frame can jump.
                self.mayJump = 1
                if impact < -30.0:
                    messenger.send("jumpHardLand")
                else:
                    messenger.send("jumpLand")
            self.priorParent = Vec3.zero()
        else:
            self.isAirborne = 1

        wishDirection, wishMagnitude, forwardInput, reverseInput = self._movementWish()
        # LocalAvatar's sprint state updates MaxGroundSpeed through
        # setWalkSpeed.  Do not multiply that configured value a second time;
        # the run input is still owned by the sprint state/FOV code.
        maxSpeed = self.MaxGroundSpeed
        if crouchDown:
            maxSpeed *= 0.55
        wishSpeed = maxSpeed * wishMagnitude

        # Auto-jump is intentional: keeping Space down jumps on the first
        # grounded frame after landing.  It does not add ground speed, so the
        # movement model still has the Source/Orange Box feel rather than
        # classic unlimited ground bunnyhopping.
        if grounded and jumpDown and self.mayJump:
            self.lifter.addVelocity(self.avatarControlJumpForce)
            self.isAirborne = 1
            grounded = False
            messenger.send("jumpStart")

        if grounded:
            if wishMagnitude <= 0.0001:
                self._applyFriction(dt)
            else:
                self._applyFriction(dt)
                self._accelerate(wishDirection, wishSpeed,
                                 self.GroundAcceleration, dt)
        elif wishMagnitude > 0.0001:
            # Orange Box air acceleration normally caps the requested air
            # speed.  Permit a deliberately larger backward wish speed so a
            # turn-plus-back input can perform accelerated backhops (ABH),
            # while ordinary forward air movement keeps the normal cap.
            backhopping = reverseInput > 0.0 and forwardInput <= 0.0
            airWishSpeed = min(wishSpeed, self.MaxAirWishSpeed)
            airAcceleration = self.AirAcceleration
            if backhopping:
                airWishSpeed = max(airWishSpeed,
                                   max(self.BackHopWishSpeed,
                                       self.MaxGroundSpeed * 1.5) *
                                   (0.55 if crouchDown else 1.0))
                airAcceleration = self.BackHopAcceleration
            self._accelerate(wishDirection,
                             airWishSpeed, airAcceleration, dt)

        if self.horizontalVelocity.length() > maxSpeed and grounded:
            self.horizontalVelocity.normalize()
            self.horizontalVelocity *= maxSpeed

        self._updateBodyHeading(wishDirection, wishMagnitude)
        self.vel = Vec3(self.horizontalVelocity)
        self.speed = self.horizontalVelocity.length() if wishMagnitude else 0.0
        self.rotationSpeed = 0.0
        self.slideSpeed = 0.0
        self.moving = bool(self.horizontalVelocity.length() > 0.01 or self.isAirborne)

        if self.moving:
            worldPosition = self.avatarNodePath.getPos(render)
            self.avatarNodePath.setFluidPos(
                render, Point3(worldPosition + self.horizontalVelocity * dt))
            messenger.send("avatarMoving")
        return Task.cont

    def getVelocity(self):
        return Vec3(self.horizontalVelocity)
