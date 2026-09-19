"""Static consistency check for the real-time street combat network surface.

Cross-checks ``astron/dclass/ttap.dc`` against the Python code without
booting a client or an AI:

* every new dc field has a receive handler with a compatible arity on the
  side(s) that receive it (``airecv`` -> AI, ``broadcast``/``ownrecv``/
  ``clrecv`` -> client);
* every ``sendUpdate(...)`` / ``sendUpdateToAvatarId(...)`` /
  ``sendUpdateToChannel(...)`` call that passes a *literal* argument list
  passes exactly as many values as the dc field declares.

Run with the bundled interpreter::

    ./Panda3D/python/ppython.exe tools/action_dc_check.py
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from panda3d.core import Filename  # noqa: E402
from panda3d.direct import DCFile  # noqa: E402

DC_PATH = os.path.join(ROOT, 'astron', 'dclass', 'ttap.dc')

# dclass -> (client files, AI files).  Subclasses are listed with their base
# so handlers may live on either level of the Python hierarchy.
CLASSES = {
    'DistributedToon': (
        ['toontown/toon/DistributedToon.py', 'toontown/toon/LocalToon.py'],
        ['toontown/toon/DistributedToonAI.py'],
    ),
    'DistributedSuitPlanner': (
        ['toontown/suit/DistributedSuitPlanner.py'],
        ['toontown/suit/DistributedSuitPlannerAI.py'],
    ),
    'DistributedSuitBase': (
        ['toontown/suit/DistributedSuitBase.py', 'toontown/suit/DistributedSuit.py'],
        ['toontown/suit/DistributedSuitBaseAI.py', 'toontown/suit/DistributedSuitAI.py'],
    ),
    'DistributedTrackCache': (
        ['toontown/action/DistributedTrackCache.py'],
        ['toontown/action/DistributedTrackCacheAI.py'],
    ),
    'ActionTutorialManager': (
        ['toontown/action/tutorial/ActionTutorialManager.py'],
        ['toontown/action/tutorial/ActionTutorialManagerAI.py'],
    ),
}

FIELDS = {
    'DistributedToon': [
        'setEquippedTracks', 'requestEquipTracks', 'requestBuyGagTier', 'gagTierPurchaseResult',
    ],
    'DistributedSuitPlanner': [
        'requestStreetRun', 'leaveStreetRun', 'setActionTier', 'setActionPressure',
        'setActionObjectives', 'actionObjectiveComplete', 'actionAnnounce',
        'requestActionTrap', 'actionTrapPlaced', 'actionTrapTriggered',
        'requestActionToonUp', 'actionToonUp',
    ],
    'DistributedSuitBase': [
        'requestFreeGagHit', 'freeGagHit', 'setActionState', 'actionAttack',
        'actionAttackResolved', 'actionStatus', 'actionDefeated',
    ],
    'DistributedTrackCache': ['setTrackReward'],
    'ActionTutorialManager': [
        'requestAdvance', 'requestSkip', 'setTutorialStep', 'setTutorialValue',
        'tutorialStepComplete', 'tutorialFinished',
    ],
}

# Files whose sendUpdate literals are checked against the fields above.
SENDER_FILES = [
    'toontown/suit/DistributedSuitBase.py', 'toontown/suit/DistributedSuit.py',
    'toontown/suit/DistributedSuitPlanner.py', 'toontown/toon/DistributedToon.py',
    'toontown/toon/LocalToon.py', 'toontown/toon/GagViewModel.py',
    'toontown/action/DistributedTrackCache.py', 'toontown/action/StreetRun.py',
    'toontown/action/tutorial/ActionTutorialManagerAI.py',
    'toontown/action/tutorial/ActionTutorialManager.py',
    'toontown/action/ui/ActionHUD.py', 'toontown/action/ui/TierSelectPanel.py',
    'toontown/action/ui/LoadoutPanel.py',
    'toontown/suit/DistributedSuitBaseAI.py', 'toontown/suit/DistributedSuitAI.py',
    'toontown/suit/DistributedSuitPlannerAI.py', 'toontown/toon/DistributedToonAI.py',
    'toontown/action/DistributedTrackCacheAI.py',
    'toontown/action/director/StreetDirectorAI.py',
    'toontown/action/cog/CogCombatControllerAI.py',
    'toontown/spellbook/MagicWordIndex.py',
]

SEND_METHODS = {'sendUpdate': 0, 'sendUpdateToAvatarId': 1, 'sendUpdateToChannel': 1}


def loadDc():
    dcf = DCFile()
    if not dcf.read(Filename.fromOsSpecific(DC_PATH)):
        raise SystemExit('could not parse %s' % DC_PATH)
    return dcf


def fieldArity(dclass, name):
    field = dclass.getFieldByName(name)
    if field is None:
        return None, None
    atomic = field.asAtomicField()
    if atomic is None:
        return None, field
    return atomic.getNumElements(), field


def parse(path):
    with open(os.path.join(ROOT, path), 'r', encoding='utf-8') as handle:
        return ast.parse(handle.read(), path)


def collectMethods(tree, names):
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name in names:
                args = item.args
                positional = len(args.args) - 1  # drop self
                minArgs = positional - len(args.defaults)
                maxArgs = None if args.vararg is not None else positional
                found.setdefault(item.name, []).append((node.name, minArgs, maxArgs))
    return found


def collectSends(tree, path, names):
    sends = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in SEND_METHODS:
            continue
        nameIndex = SEND_METHODS[method]
        if len(node.args) <= nameIndex:
            continue
        nameNode = node.args[nameIndex]
        if not isinstance(nameNode, ast.Constant) or nameNode.value not in names:
            continue
        argList = node.args[nameIndex + 1] if len(node.args) > nameIndex + 1 else None
        count = len(argList.elts) if isinstance(argList, (ast.List, ast.Tuple)) else None
        sends.append((path, node.lineno, nameNode.value, count))
    return sends


def main():
    dcf = loadDc()
    problems = []
    checked = 0
    allNames = set()
    arities = {}
    for className, names in FIELDS.items():
        dclass = dcf.getClassByName(className)
        if dclass is None:
            problems.append('dclass %s missing from dc' % className)
            continue
        clientFiles, aiFiles = CLASSES[className]
        clientMethods, aiMethods = {}, {}
        for path in clientFiles:
            for key, value in collectMethods(parse(path), set(names)).items():
                clientMethods.setdefault(key, []).extend(value)
        for path in aiFiles:
            for key, value in collectMethods(parse(path), set(names)).items():
                aiMethods.setdefault(key, []).extend(value)
        for name in names:
            arity, field = fieldArity(dclass, name)
            if field is None:
                problems.append('%s.%s missing from dc' % (className, name))
                continue
            if arity is None:
                continue
            allNames.add(name)
            arities[name] = arity
            needsAi = field.isAirecv()
            needsClient = field.isBroadcast() or field.isOwnrecv() or field.isClrecv()
            for label, needed, methods in (('AI', needsAi, aiMethods), ('client', needsClient, clientMethods)):
                if not needed:
                    continue
                checked += 1
                defs = methods.get(name)
                if not defs:
                    problems.append('%s.%s: no %s handler defined' % (className, name, label))
                    continue
                for owner, minArgs, maxArgs in defs:
                    if arity < minArgs or (maxArgs is not None and arity > maxArgs):
                        problems.append('%s.%s: %s handler %s.%s accepts %s..%s args, dc has %d'
                                        % (className, name, label, owner, name, minArgs,
                                           'inf' if maxArgs is None else maxArgs, arity))
    unchecked = 0
    for path in SENDER_FILES:
        for filePath, line, name, count in collectSends(parse(path), path, allNames):
            if count is None:
                unchecked += 1
                continue
            checked += 1
            if count != arities[name]:
                problems.append('%s:%d sends %s with %d args, dc has %d'
                                % (filePath, line, name, count, arities[name]))
    for problem in problems:
        print('FAIL ' + problem)
    print('%d checks, %d non-literal sends skipped, %d problems' % (checked, unchecked, len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
