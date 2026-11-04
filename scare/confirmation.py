"""Require repeated, spatially consistent animal predictions before activation."""


def overlap(a, b):
    x1,y1,x2,y2=a;u1,v1,u2,v2=b
    intersection=max(0,min(x2,u2)-max(x1,u1))*max(0,min(y2,v2)-max(y1,v1))
    union=max(0,x2-x1)*max(0,y2-y1)+max(0,u2-u1)*max(0,v2-v1)-intersection
    return intersection/union if union>0 else 0.0


class TargetConfirmer:
    def __init__(self, frames=3, iou=.3, max_gap=1.0):
        if not isinstance(frames,int) or frames<1: raise ValueError('confirm_frames must be a positive integer')
        if not 0<iou<=1: raise ValueError('confirm_iou must be in (0,1]')
        if not 0<max_gap<float('inf'): raise ValueError('confirmation_gap_seconds must be finite and positive')
        self.frames,self.iou,self.max_gap=frames,iou,max_gap
        self.previous=[];self.last_time=None

    def update(self, detections, now):
        old=self.previous if self.last_time is not None and 0<=now-self.last_time<=self.max_gap else []
        used=set();current=[];confirmed=[]
        for det in detections:
            matches=[(overlap(det.box,prev.box),i,count) for i,(prev,count) in enumerate(old)
                     if i not in used and prev.label==det.label]
            score,index,count=max(matches,default=(0,None,0))
            if score>=self.iou:
                used.add(index);count+=1
            else: count=1
            current.append((det,count))
            if count>=self.frames:confirmed.append(det)
        self.previous=current;self.last_time=now
        return confirmed
