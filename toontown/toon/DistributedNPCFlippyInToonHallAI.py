from .DistributedNPCToonAI import *


class DistributedNPCFlippyInToonHallAI(DistributedNPCToonAI):

    def __init__(self, air, npcId, questCallback=None, hq=0):
        DistributedNPCToonAI.__init__(self, air, npcId, questCallback)

    def avatarEnter(self):
        DistributedNPCToonBaseAI.avatarEnter(self)
        avId = self.air.getAvatarIdFromSender()
        self.notify.debug(f"{avId} speaking to flippy")

        self.air.questManager.requestInteract(avId, self)
