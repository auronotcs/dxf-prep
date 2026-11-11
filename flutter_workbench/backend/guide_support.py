"""Atomic guide drafts using the captured core's non-machining Guide model."""
import copy
import math
from cam_core.guides import Guide
from cam_core.model import new_id


def coordinate(value):
    if isinstance(value,bool): raise ValueError('Guide coordinate must be a finite number.')
    result=float(value)
    if not math.isfinite(result): raise ValueError('Guide coordinate must be a finite number.')
    return result


def boolean(value):
    if type(value) is not bool: raise ValueError('Choose a valid guide visibility or lock setting.')
    return value


def add(project,params):
    guide=Guide(str(params['orientation']),coordinate(params['position']),name=str(params.get('name','')))
    guide.validate()
    project.guides.append(guide)


class GuideDraft:
    def __init__(self): self.cached=None

    def preview(self,project,params,revision,digest):
        self.cached=None
        guides=copy.deepcopy(project.guides)
        actions=params.get('actions')
        if not isinstance(actions,list) or len(actions)>2000: raise ValueError('Use a bounded list of guide edits.')
        for action in actions:
            operation=action['operation']
            if operation=='add':
                identity=action.get('id')
                if not isinstance(identity,str) or not identity or any(g.id==identity for g in guides):
                    raise ValueError('New guide identity must be unique.')
                guide=Guide(str(action['orientation']),coordinate(action['position']),name=str(action.get('name','')),id=identity)
                guide.validate();guides.append(guide)
                continue
            if operation in {'show_all','lock_all'}:
                value=boolean(action['value'])
                for guide in guides: setattr(guide,'visible' if operation=='show_all' else 'locked',value)
                continue
            guide=next((g for g in guides if g.id==action.get('id')),None)
            if guide is None: raise ValueError('That guide no longer exists.')
            if operation in {'edit','delete'} and guide.locked: raise ValueError('Unlock the guide before moving or deleting it.')
            if operation=='edit':
                guide.position=coordinate(action['position'])
                guide.name=str(action.get('name',guide.name))
            elif operation=='delete': guides.remove(guide)
            elif operation=='visible': guide.visible=boolean(action['value'])
            elif operation=='locked': guide.locked=boolean(action['value'])
            else: raise ValueError('Choose a supported guide action.')
        if len(guides)>1000: raise ValueError('Keep this drawing to at most 1000 guides.')
        for guide in guides: guide.validate()
        token=new_id('guides-preview')
        self.cached=dict(token=token,revision=revision,digest=digest,guides=guides)
        return dict(token=token,revision=revision,guides=[g.to_dict() for g in guides])

    def apply(self,project,params,revision,digest):
        cached=self.cached
        if not cached or params.get('token')!=cached['token'] or revision!=cached['revision'] or digest!=cached['digest']:
            raise ValueError('The drawing changed. Reopen Guide Manager to review it again.')
        project.guides=copy.deepcopy(cached['guides'])
        self.cached=None
