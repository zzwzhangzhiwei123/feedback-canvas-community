"""Compact rectangle packing without changing image dimensions."""
import math

def pack_rectangles(sizes, gap=8.0):
    if not sizes: return []
    padded=[(max(1,w)+gap,max(1,h)+gap) for w,h in sizes]
    target=max(max(w for w,h in padded),math.sqrt(sum(w*h for w,h in padded)))
    free=[(0.,0.,target,sum(h for w,h in padded))]
    positions=[None]*len(sizes)
    for index in sorted(range(len(sizes)),key=lambda i:(-max(padded[i]),-padded[i][0]*padded[i][1],i)):
        w,h=padded[index]
        choices=[(min(fw-w,fh-h),max(fw-w,fh-h),y,x,j) for j,(x,y,fw,fh) in enumerate(free) if fw+1e-8>=w and fh+1e-8>=h]
        _,_,y,x,j=min(choices)
        positions[index]=(x,y)
        split=[]
        for fx,fy,fw,fh in free:
            if x>=fx+fw or x+w<=fx or y>=fy+fh or y+h<=fy:
                split.append((fx,fy,fw,fh));continue
            if x>fx: split.append((fx,fy,x-fx,fh))
            if x+w<fx+fw: split.append((x+w,fy,fx+fw-x-w,fh))
            if y>fy: split.append((fx,fy,fw,y-fy))
            if y+h<fy+fh: split.append((fx,y+h,fw,fy+fh-y-h))
        free=[]
        for a in sorted(set(split)):
            ax,ay,aw,ah=a
            if not any(a!=b and ax>=b[0]-1e-8 and ay>=b[1]-1e-8 and ax+aw<=b[0]+b[2]+1e-8 and ay+ah<=b[1]+b[3]+1e-8 for b in split): free.append(a)
    return positions
