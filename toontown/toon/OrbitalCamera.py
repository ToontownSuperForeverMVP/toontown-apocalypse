"""Mouse-locked Source-style camera used by the local Toon."""

import math

from panda3d.core import (BitMask32, CollisionNode, CollisionSegment,
                          CollisionTraverser, CollisionHandlerQueue, LineSegs,
                          NodePath, Point3, Vec3, WindowProperties)
from direct.directnotify import DirectNotifyGlobal
from direct.fsm.FSM import FSM
from direct.showbase.InputStateGlobal import inputState
from direct.task import Task
from direct.task.TaskManagerGlobal import taskMgr

from otp.otpbase import OTPGlobals
from toontown.toon.ParamObj import ParamObj
from toontown.toon.GagViewModel import GagViewModel


class OrbitalCamera(FSM, NodePath, ParamObj):
    """True first person with a right-shoulder, Fortnite-like third person."""

    notify = DirectNotifyGlobal.directNotify.newCategory("OrbitalCamera")

    class ParamSet(ParamObj.ParamSet):
        Params = {"camOffset": Vec3(2.75, -12.0, 1.35)}

    UpdateTaskName = "SourceCameraUpdateTask"
    ReadMouseTaskName = "SourceCameraReadMouseTask"
    CollisionCheckTaskName = "SourceCameraCollisionTask"
    TopNodeName = "SourceCamera"
    MinP = -80.0
    MaxP = 80.0
    MinDistance = 4.0
    MaxDistance = 22.0
    ShoulderOffset = 2.75
    ShoulderHeight = 1.35
    SourceFov = 90.0

    def __init__(self, subject):
        ParamObj.__init__(self)
        NodePath.__init__(self, self.TopNodeName)
        FSM.__init__(self, "OrbitalCamera")
        self.subject = subject
        self.setDefaultParams()
        self.firstPerson = True
        self.mouseControl = False
        self.ignoreRMB = True
        self.cam_toggled = False
        self.viewYaw = subject.getH(render)
        self.viewPitch = 0.0
        self.distance = 12.0
        self.presetPos = 0
        self.mouseDelta = (0.0, 0.0)
        self.origMousePos = (0, 0)
        self._rmbToken = inputState.watchWithModifiers("RMB", "mouse3")
        self._cameraCollision = None
        self._cameraCollisionNode = None
        self._cameraCollisionNp = None
        self._cameraQueue = None
        self._cameraTrav = None
        self._savedFov = None
        self._crosshair = None
        self._shake = 0.0
        self._followErrorReported = False
        self.gagViewModel = GagViewModel(camera, subject)
        self.request("Off")

    def destroy(self):
        self.stop()
        self._rmbToken.release()
        self._destroyCollision()
        self.gagViewModel.destroy()
        self.gagViewModel = None
        self._destroyCrosshair()
        self.ignoreAll()
        if not self.isEmpty():
            self.removeNode()
        self.subject = None
        FSM.cleanup(self)
        ParamObj.destroy(self)

    def getViewYaw(self):
        return self.viewYaw

    def enterActive(self):
        self.cam_toggled = True
        self.reparentTo(render)
        self._startCollision()
        self.accept("tab", self.toggleFirstPerson)
        self.accept("wheel_up", self._handleWheelUp)
        self.accept("wheel_down", self._handleWheelDown)
        self.accept("page_up", self._handleWheelUp)
        self.accept("page_down", self._handleWheelDown)
        self.accept("mouse1", self._pressTrigger)
        self.accept("mouse1-up", self._releaseTrigger)
        self.accept("1", self._selectSlot, [0])
        self.accept("2", self._selectSlot, [1])
        self.accept("3", self._selectSlot, [2])
        self._startMouseControl()
        self._setCursorHidden(True)
        self._savedFov = base.camLens.getFov()
        base.camLens.setFov(self.SourceFov)
        self._createCrosshair()
        self.gagViewModel.setVisible(self.firstPerson)
        self.subject.controlManager.setTurn(0)
        # GravityWalker runs at priority 25.  Update the view basis after the
        # movement step so the camera follows the new avatar position, while
        # mouse sampling itself happens before movement for the same frame.
        taskMgr.add(self._followTask, self.UpdateTaskName, priority=30)

    def exitActive(self):
        taskMgr.remove(self.UpdateTaskName)
        self._stopMouseControl()
        self._destroyCollision()
        self.ignore("tab")
        self.ignore("wheel_up")
        self.ignore("wheel_down")
        self.ignore("page_up")
        self.ignore("page_down")
        self.ignore("mouse1")
        self.ignore("mouse1-up")
        self.ignore("1")
        self.ignore("2")
        self.ignore("3")
        self._setCursorHidden(False)
        self.gagViewModel.setTrigger(False)
        self.gagViewModel.setVisible(False)
        self._destroyCrosshair()
        self._showAvatar(True)
        if self._savedFov is not None:
            base.camLens.setFov(self._savedFov[0], self._savedFov[1])
            self._savedFov = None
        self.subject.controlManager.setTurn(0)
        self.cam_toggled = False

    def start(self):
        if not self.isActive():
            self.request("Active")

    def stop(self):
        if self.isActive():
            self.request("Off")
            self.subject.setSpeed(0, 0, 0)

    def isActive(self):
        return self.state == "Active"

    def _setCursorHidden(self, hidden):
        props = WindowProperties()
        props.setCursorHidden(hidden)
        base.win.requestProperties(props)

    def _startMouseControl(self):
        self.mouseControl = True
        pointer = base.win.getPointer(0)
        self.origMousePos = (pointer.getX(), pointer.getY())
        center = (base.win.getXSize() // 2, base.win.getYSize() // 2)
        base.win.movePointer(0, center[0], center[1])
        properties = WindowProperties()
        properties.setMouseMode(WindowProperties.MRelative)
        base.win.requestProperties(properties)
        taskMgr.add(self._readMouseTask, self.ReadMouseTaskName, priority=10)

    def _stopMouseControl(self):
        self.mouseControl = False
        taskMgr.remove(self.ReadMouseTaskName)
        properties = WindowProperties()
        properties.setMouseMode(WindowProperties.MAbsolute)
        base.win.requestProperties(properties)
        if hasattr(base, "win") and base.win:
            base.win.movePointer(0, int(self.origMousePos[0]), int(self.origMousePos[1]))

    def enableMouseControl(self, pressed=True, toggle=False):
        if self.isActive() and not self.mouseControl:
            self._startMouseControl()
            self._setCursorHidden(True)

    def disableMouseControl(self, pressed=True, disabledByMouse=True):
        if self.mouseControl:
            self._stopMouseControl()
            self._setCursorHidden(False)

    def _readMouseTask(self, task):
        if not self.mouseControl or not base.mouseWatcherNode.hasMouse():
            self.mouseDelta = (0.0, 0.0)
            return Task.cont
        width, height = base.win.getXSize(), base.win.getYSize()
        pointer = base.win.getPointer(0)
        dx = pointer.getX() - width // 2
        dy = pointer.getY() - height // 2
        base.win.movePointer(0, width // 2, height // 2)

        if dx or dy:
            sensitivityX = base.settings.get("camSensitivityX") or 0.15
            sensitivityY = base.settings.get("camSensitivityY") or 0.10
            self.viewYaw -= dx * sensitivityX
            self.viewPitch = max(self.MinP, min(self.MaxP,
                                                 self.viewPitch - dy * sensitivityY))
        return Task.cont

    def _followTask(self, task):
        # A transient empty NodePath can happen while the local avatar is
        # being reparented or regenerated during a zone/state transition.
        # This task owns the live camera, so returning Task.done here silently
        # strands the player with a frozen camera while movement/chat continue.
        if self.subject is None or self.subject.isEmpty():
            return Task.cont

        try:
            height = self.subject.getHeight() if hasattr(self.subject, "getHeight") else 2.0
            crouchScale = 0.72 if getattr(self.subject, 'sourceCrouched', False) else 1.0
            pivotHeight = height * 0.52 * crouchScale
            eyeHeight = height * 0.82 * crouchScale
            self.setPos(self.subject.getPos(render) + Vec3(0, 0, pivotHeight))
            self.setHpr(self.viewYaw, self.viewPitch, 0)

            if self.firstPerson:
                # Other gameplay movies can temporarily reparent the global
                # camera. Reclaim it on the next normal gameplay frame.
                camera.reparentTo(self)
                camera.setPos(0, 0, eyeHeight - pivotHeight)
                camera.setHpr(0, 0, 0)
                self._applyShake()
                self._showAvatar(False)
                self.subject.updateSourceHeadFacing(self.viewYaw)
                self.subject.setSourceBodyHeading(self.viewYaw)
            else:
                self._updateThirdPersonCamera()
                self.subject.updateSourceHeadFacing(self.viewYaw)
            overlayVisible = self.firstPerson and self._isFreeRoamView()
            self._setCrosshairVisible(overlayVisible)
            self.gagViewModel.setVisible(overlayVisible)
            self._followErrorReported = False
        except Exception:
            # A bad frame must not remove the only camera update task. Keep
            # it alive and report the first failure for the next log.
            if not self._followErrorReported:
                self.notify.warning('Camera follow update failed; retrying next frame')
                import traceback
                traceback.print_exc()
                self._followErrorReported = True
        return Task.cont

    def _isFreeRoamView(self):
        playGame = getattr(getattr(base, 'cr', None), 'playGame', None)
        place = getattr(playGame, 'place', None)
        if place is None and playGame is not None:
            place = playGame.getPlace()
        if place is None or not hasattr(place, 'getState'):
            return True
        return place.getState() in ('walk', 'start', 'quietZone')

    def _pressTrigger(self):
        if self.firstPerson and self._isFreeRoamView():
            self.gagViewModel.setTrigger(True)

    def _releaseTrigger(self):
        self.gagViewModel.setTrigger(False)

    def _selectSlot(self, slotIndex):
        if self.firstPerson:
            self.gagViewModel.select(slotIndex)

    def addShake(self, strength):
        """Kick the first-person camera; decays over the next few frames."""
        self._shake = min(1.5, self._shake + max(0.0, strength))

    def _applyShake(self):
        if self._shake <= 0.001:
            return
        dt = max(0.0, min(0.1, globalClock.getDt()))
        amount = self._shake
        angle = globalClock.getFrameTime() * 47.0
        camera.setHpr(math.sin(angle) * 2.2 * amount, math.cos(angle * 1.3) * 1.6 * amount,
                      math.sin(angle * 0.7) * 1.1 * amount)
        self._shake = max(0.0, self._shake - dt * 4.5)

    def _createCrosshair(self):
        if self._crosshair:
            self._setCrosshairVisible(self.firstPerson)
            return
        lines = LineSegs('FirstPersonCrosshair')
        lines.setThickness(2.0)
        lines.setColor(1.0, 1.0, 1.0, 0.9)
        lines.moveTo(-0.018, 0.0, 0.0)
        lines.drawTo(-0.005, 0.0, 0.0)
        lines.moveTo(0.005, 0.0, 0.0)
        lines.drawTo(0.018, 0.0, 0.0)
        lines.moveTo(0.0, -0.018, 0.0)
        lines.drawTo(0.0, -0.005, 0.0)
        lines.moveTo(0.0, 0.005, 0.0)
        lines.drawTo(0.0, 0.018, 0.0)
        self._crosshair = base.aspect2d.attachNewNode(lines.create())
        self._crosshair.setBin('fixed', 100)
        self._crosshair.setDepthTest(False)
        self._crosshair.setDepthWrite(False)
        self._setCrosshairVisible(self.firstPerson)

    def _setCrosshairVisible(self, visible):
        if self._crosshair:
            if visible:
                self._crosshair.show()
            else:
                self._crosshair.hide()

    def _destroyCrosshair(self):
        if self._crosshair:
            self._crosshair.removeNode()
            self._crosshair = None

    def _startCollision(self):
        self._cameraCollision = CollisionSegment(0, 0, 0,
                                                  self.ShoulderOffset,
                                                  -self.distance,
                                                  self.ShoulderHeight)
        self._cameraCollisionNode = CollisionNode("SourceCameraCollision")
        self._cameraCollisionNode.addSolid(self._cameraCollision)
        self._cameraCollisionNode.setFromCollideMask(
            OTPGlobals.CameraBitmask | OTPGlobals.CameraTransparentBitmask |
            OTPGlobals.FloorBitmask)
        self._cameraCollisionNode.setIntoCollideMask(BitMask32.allOff())
        self._cameraCollisionNp = self.attachNewNode(self._cameraCollisionNode)
        self._cameraQueue = CollisionHandlerQueue()
        self._cameraTrav = CollisionTraverser("SourceCameraTraverser")
        self._cameraTrav.addCollider(self._cameraCollisionNp, self._cameraQueue)
        taskMgr.add(self._collisionTask, self.CollisionCheckTaskName, priority=45)

    def _destroyCollision(self):
        taskMgr.remove(self.CollisionCheckTaskName)
        if self._cameraTrav and self._cameraCollisionNp:
            self._cameraTrav.removeCollider(self._cameraCollisionNp)
        if self._cameraCollisionNp:
            self._cameraCollisionNp.removeNode()
        self._cameraCollision = None
        self._cameraCollisionNode = None
        self._cameraCollisionNp = None
        self._cameraQueue = None
        self._cameraTrav = None

    def _collisionTask(self, task):
        if self.firstPerson or not self._cameraTrav:
            self._showAvatar(False)
            return Task.cont
        crouchScale = 0.72 if getattr(self.subject, 'sourceCrouched', False) else 1.0
        desired = Point3(self.ShoulderOffset,
                         -self.distance,
                         self.ShoulderHeight * crouchScale)
        self._cameraCollision.setPointB(desired)
        self._cameraTrav.traverse(render)
        cameraPos = desired
        if self._cameraQueue.getNumEntries():
            self._cameraQueue.sortEntries()
            entry = self._cameraQueue.getEntry(0)
            if entry.hasSurfacePoint():
                cameraPos = entry.getSurfacePoint(self) + entry.getSurfaceNormal(self) * 0.35
        camera.reparentTo(self)
        camera.setPos(cameraPos)
        camera.lookAt(self, 0, 0, 0.35 * crouchScale)
        self._showAvatar(True)
        return Task.cont

    def _updateThirdPersonCamera(self):
        self._showAvatar(True)

    def _showAvatar(self, visible):
        geom = self.subject.getGeomNode()
        if self.subject.isDisguised or not visible:
            if self.firstPerson and not self.subject.isDisguised:
                # Keep the animated torso in the camera view so the Toon arms
                # and hands remain visible with the gag prop.  The head and
                # legs are hidden to avoid seeing the avatar from inside it.
                geom.show()
                self._setAvatarPartVisible('head', False)
                self._setAvatarPartVisible('legs', False)
                self._setAvatarPartVisible('torso', True)
            else:
                geom.hide()
        else:
            geom.show()
            self._setAvatarPartVisible('head', True)
            self._setAvatarPartVisible('legs', True)
            self._setAvatarPartVisible('torso', True)

    def _subjectPart(self, partName):
        """Return one of the subject's parts under a LOD name it really has.

        ``Actor.getPart`` defaults to a LOD called ``lodRoot``, which only
        exists on single-LOD models.  Toons keep their parts under numbered
        LODs, so asking for the default silently returns None *and* logs
        ``no lod named: lodRoot`` -- and this runs every frame.
        """
        lodNames = self.subject.getLODNames()
        if lodNames:
            return self.subject.getPart(partName, lodNames[0])
        # A partially generated or single-geometry Actor may have no named
        # LOD at all.  Calling Actor.getPart without a real LOD makes Panda
        # spam "no lod named: lodRoot" every frame; a normal scene-graph
        # lookup is quiet and still finds the part when it exists.
        return self.subject.find('**/%s' % partName)

    def _setAvatarPartVisible(self, partName, visible):
        """Apply a part visibility change when the Toon has generated it."""
        part = self._subjectPart(partName)
        if part is None or part.isEmpty():
            return
        if visible:
            part.show()
        else:
            part.hide()

    def toggleFirstPerson(self):
        self.firstPerson = not self.firstPerson
        self._showAvatar(not self.firstPerson)
        overlayVisible = self.firstPerson and self._isFreeRoamView()
        self._setCrosshairVisible(overlayVisible)
        self.gagViewModel.setVisible(overlayVisible)

    def _handleWheelUp(self):
        if self.firstPerson:
            self.gagViewModel.cycle(-1)
        else:
            self.distance = max(self.MinDistance, self.distance - 0.75)

    def _handleWheelDown(self):
        if self.firstPerson:
            self.gagViewModel.cycle(1)
        else:
            self.distance = min(self.MaxDistance, self.distance + 0.75)

    def acceptWheel(self):
        self.accept("wheel_up", self._handleWheelUp)
        self.accept("wheel_down", self._handleWheelDown)

    def ignoreWheel(self):
        self.ignore("wheel_up")
        self.ignore("wheel_down")

    def acceptTab(self):
        self.accept("tab", self.toggleFirstPerson)

    def ignoreTab(self):
        self.ignore("tab")

    def getCamOffset(self):
        return Vec3(self.ShoulderOffset, -self.distance, self.ShoulderHeight)

    def setCamOffset(self, camOffset):
        self.ShoulderOffset = float(camOffset[0])
        self.distance = max(self.MinDistance, min(self.MaxDistance, abs(float(camOffset[1]))))
        self.ShoulderHeight = float(camOffset[2])

    def applyCamOffset(self):
        pass

    def setPresetPos(self, presetIndex, transition=True):
        self.presetPos = presetIndex
        self.distance = (12.0, 18.0, 7.0)[presetIndex % 3]

    def setCameraPos(self, y, h, p, transition=True):
        self.viewYaw = h
        self.viewPitch = p
        self.distance = max(self.MinDistance, min(self.MaxDistance, abs(y)))

    def getCurrentOrNextState(self):
        return self.state

    def oobeEnabled(self):
        return hasattr(base, "oobeMode") and base.oobeMode
