"""Gag Training Cache (AI side).

A one-shot treasure dropped on the street by the :class:`StreetDirectorAI`.
Grabbing it permanently discovers a random gag track the Toon does not own
yet (or pays out jellybeans when every track is already known).
"""

from panda3d.core import Point3
from direct.task import Task

from toontown.action import ActionGlobals
from toontown.safezone import DistributedTreasureAI


class DistributedTrackCacheAI(DistributedTreasureAI.DistributedTreasureAI):

    DELETE_DELAY = 3.0
    GRAB_DISTANCE = 12.0

    def __init__(self, air, director, x, y, z):
        DistributedTreasureAI.DistributedTreasureAI.__init__(self, air, None, x, y, z)
        self.director = director
        self.grabbed = False

    def delete(self):
        taskMgr.remove(self.taskName('cacheDelete'))
        director = self.director
        self.director = None
        if director is not None:
            director.onCacheDeleted(self)
        DistributedTreasureAI.DistributedTreasureAI.delete(self)

    def requestGrab(self):
        avId = self.air.getAvatarIdFromSender()
        toon = self.air.doId2do.get(avId)
        if toon is None or self.grabbed:
            self.d_setReject()
            return
        try:
            distance = (Point3(toon.getPos()) - Point3(*self.pos)).length()
        except Exception:
            distance = 0.0
        if distance > self.GRAB_DISTANCE:
            self.d_setReject()
            return
        self.grabbed = True
        track, beans = -1, 0
        if self.director is not None:
            track, beans = self.director.onCacheGrabbed(avId, toon)
        self.d_setGrab(avId)
        self.sendUpdateToAvatarId(avId, 'setTrackReward', [track, beans])
        taskMgr.doMethodLater(self.DELETE_DELAY, self.__deleteTask, self.taskName('cacheDelete'))

    def __deleteTask(self, task):
        if not self.isDeleted():
            self.requestDelete()
        return Task.done
