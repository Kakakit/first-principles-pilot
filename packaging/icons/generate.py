"""Render our small SVG primitive set with Pillow; no runtime icon dependency."""
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageColor

ROOT=Path(__file__).resolve().parent


def render():
    svg=ET.parse(ROOT/'fpilot.svg').getroot()
    image=Image.new('RGBA',(1024,1024));draw=ImageDraw.Draw(image)
    stops=svg.findall('.//{*}stop')
    colors=[ImageColor.getrgb(s.attrib['stop-color']) for s in stops]
    for node in svg:
        kind=node.tag.split('}')[-1];a=node.attrib
        if kind=='rect':
            mask=Image.new('L',image.size);md=ImageDraw.Draw(mask)
            x,y,w,h=[int(a[k]) for k in ('x','y','width','height')]
            md.rounded_rectangle((x,y,x+w,y+h),int(a['rx']),fill=255)
            gradient=Image.new('RGBA',image.size);gd=ImageDraw.Draw(gradient)
            for row in range(1024):
                t=max(0,min(1,(row-y)/h));color=tuple(round(lo+(hi-lo)*t) for lo,hi in zip(*colors))
                gd.line((0,row,1024,row),fill=color+(255,))
            image.paste(gradient,(0,0),mask);draw=ImageDraw.Draw(image)
        elif kind=='polyline':
            points=[tuple(map(int,p.split(','))) for p in a['points'].split()]
            width=int(a['stroke-width']);color=a['stroke'];draw.line(points,fill=color,width=width,joint='curve')
            for px,py in points:draw.ellipse((px-width/2,py-width/2,px+width/2,py+width/2),fill=color)
        elif kind=='circle':
            x,y,r=[int(a[k]) for k in ('cx','cy','r')];draw.ellipse((x-r,y-r,x+r,y+r),fill=a['fill'])
    return image


if __name__=='__main__':
    image=render();image.save(ROOT/'fpilot.png')
    image.save(ROOT/'fpilot.ico',sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
    image.save(ROOT/'fpilot.icns')
