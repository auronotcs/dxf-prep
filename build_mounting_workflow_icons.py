"""Central vector definitions with matching supersampled PNG exports."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / 'mounting-workflow-icons'
SIZES = (16, 20, 24, 32, 48, 64)
# All coordinates share a 24-unit optical box. B=body, A=feature, F=body fill.
ICONS = {
 'mounting.studs': [('poly',[(4,3),(18,3),(18,21),(4,21)],'B','F'),('circle',(9,8,2),'A',None),('circle',(13,16,2),'A',None)],
 'mounting.backer': [('poly',[(3,7),(15,7),(15,21),(3,21)],'B','F'),('poly',[(9,3),(21,3),(21,16),(9,16)],'B',None),('line',[(7,12),(12,8)],'A',None),('line',[(11,18),(16,14)],'A',None)],
 'mounting.keyholes': [('poly',[(4,2),(20,2),(20,22),(4,22)],'B','F'),('poly',[(10,6),(14,6),(14,11),(16,14),(16,17),(14,19),(10,19),(8,17),(8,14),(10,11)],'A',None)],
 'mounting.edge_rail': [('poly',[(8,3),(16,3),(16,16),(8,16)],'B','F'),('poly',[(3,18),(21,18),(21,21),(3,21)],'B','F'),('line',[(10,21),(10,12)],'A',None),('line',[(14,21),(14,12)],'A',None)],
 'mounting.bent_tabs': [('poly',[(4,10),(16,10),(21,15),(9,15)],'B','F'),('poly',[(9,15),(9,6),(13,6),(13,15)],'A',None),('line',[(9,15),(13,15)],'A',None),('line',[(4,10),(4,7),(7,7)],'B',None)],
 'mounting.tab_slot': [('poly',[(7,2),(17,2),(17,10),(14,10),(14,15),(10,15),(10,10),(7,10)],'B','F'),('poly',[(3,17),(21,17),(21,22),(3,22)],'B','F'),('line',[(9,19.5),(15,19.5)],'A',None)],
 'mounting.puzzle': [('poly',[(2,4),(11,4),(11,8),(14,7),(16,9),(16,11),(14,13),(11,12),(11,20),(2,20)],'B','F'),('line',[(13,4),(22,4),(22,20),(13,20),(13,15)],'A',None)],
 'mounting.pattern': [('poly',[(4,2),(16,2),(20,6),(20,22),(4,22)],'B',None),('line',[(16,2),(16,6),(20,6)],'B',None),('line',[(7,11),(13,11)],'A',None),('line',[(10,8),(10,14)],'A',None),('line',[(11,18),(17,18)],'A',None),('line',[(14,15),(14,21)],'A',None)],
}
LABELS = ['Stud holes','Backer + pins','Keyholes','Edge rail','Bent tabs','Tab + slot rail','Puzzle panels','Install pattern']

def render(key, palette, size):
    scale=size*6/24
    im=Image.new('RGBA',(size*6,size*6))
    draw=ImageDraw.Draw(im)
    elements=[]
    for kind,points,stroke,fill in ICONS[key]:
        color=palette[stroke]; fc=palette[fill] if fill else 'none'
        if kind=='circle':
            x,y,r=points
            draw.ellipse(tuple(v*scale for v in (x-r,y-r,x+r,y+r)),outline=color,width=round(1.5*scale))
            elements.append(f'<circle cx="{x}" cy="{y}" r="{r}" stroke="{color}" fill="{fc}"/>')
        else:
            p=[(x*scale,y*scale) for x,y in points]
            if kind=='poly' and fill: draw.polygon(p,fill=fc)
            draw.line(p+[p[0]] if kind=='poly' else p,fill=color,width=round(1.5*scale),joint='curve')
            for x,y in (p if kind=='line' else []):
                r=.75*scale; draw.ellipse((x-r,y-r,x+r,y+r),fill=color)
            tag='polygon' if kind=='poly' else 'polyline'
            xy=' '.join(f'{x},{y}' for x,y in points)
            elements.append(f'<{tag} points="{xy}" stroke="{color}" fill="{fc}"/>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'+''.join(elements)+'</g></svg>'
    return im.resize((size,size),Image.Resampling.LANCZOS),svg

def main():
    palettes={'original':{'B':'#087FCA','A':'#ED8500','F':'#CFEAFF'},'dark':{'B':'#55BDFF','A':'#FFB047','F':'#183E58'}}
    sheet=Image.new('RGB',(1120,360),'#f4f6f8'); d=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',15)
    for row,(theme,palette) in enumerate(palettes.items()):
        out=ROOT/theme; out.mkdir(parents=True,exist_ok=True)
        bg='#f4f6f8' if theme=='original' else '#242A31'
        d.rectangle((0,row*180,1120,(row+1)*180),fill=bg)
        for col,(key,label) in enumerate(zip(ICONS,LABELS)):
            for size in SIZES:
                im,svg=render(key,palette,size); im.save(out/f'{key}-{size}.png')
            (out/f'{key}.svg').write_text(svg,encoding='utf-8')
            for size,x,y in [(48,46,20),(16,40,89),(20,67,87),(24,98,85)]:
                im,_=render(key,palette,size); sheet.paste(im,(col*140+x,row*180+y),im)
            d.text((col*140+8,row*180+125),label,font=font,fill='#253746' if row==0 else '#E3EDF5')
    sheet.save(ROOT/'review.png')
    (ROOT/'manifest.json').write_text(json.dumps({'schema':1,'viewBox':[0,0,24,24],'sizes':SIZES,'themes':list(palettes),'commands':list(ICONS),'generator':'build_mounting_workflow_icons.py'},indent=2))

if __name__=='__main__': main()
