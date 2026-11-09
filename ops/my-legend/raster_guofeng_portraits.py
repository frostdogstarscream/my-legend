"""Render original posed Spine triangles to transparent life-pose portraits."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/gfl-guofeng-presentation-20261102'
CONTENT=ROOT/'runtime/my-legend-spine/content'

def main():
    portraits=json.loads((OUT/'posed-meshes.json').read_text(encoding='utf-8'))
    textures={}
    for portrait in portraits:
        meshes=portrait['meshes'];points=np.concatenate([np.array(m['vertices']).reshape(-1,2) for m in meshes])
        lo=points.min(axis=0);hi=points.max(axis=0)
        width,height=768,864;scale=min((width-96)/(hi[0]-lo[0]),(height-96)/(hi[1]-lo[1]))
        center=(lo+hi)/2;frame=Image.new('RGBA',(width,height))
        for mesh in meshes:
            if mesh['page'] not in textures:
                rgba=np.array(Image.open(CONTENT/mesh['page']).convert('RGBA'),dtype=np.float32)
                rgba[:,:,:3]=np.minimum(255,rgba[:,:,:3]*255/np.maximum(rgba[:,:,3:4],1))
                textures[mesh['page']]=Image.fromarray(rgba.astype('uint8'))
            texture=textures[mesh['page']]
            uv=np.array(mesh['uvs']).reshape(-1,2)*texture.size
            dst=(np.array(mesh['vertices']).reshape(-1,2)-center)*[scale,-scale]+[width/2,height/2]
            for indices in np.array(mesh['triangles']).reshape(-1,3):
                target=dst[indices];source=uv[indices]
                left,top=np.maximum(0,np.floor(target.min(axis=0))).astype(int)
                right,bottom=np.minimum([width,height],np.ceil(target.max(axis=0))).astype(int)
                if right<=left or bottom<=top:continue
                local=target-[left,top];matrix=np.column_stack((local,np.ones(3)))
                if abs(np.linalg.det(matrix))<1e-8:continue
                coeff=np.linalg.solve(matrix,source).T.flatten()
                piece=texture.transform((right-left,bottom-top),Image.Transform.AFFINE,coeff,Image.Resampling.BILINEAR)
                mask=Image.new('L',piece.size);ImageDraw.Draw(mask).polygon([tuple(p) for p in local],fill=255)
                alpha=np.array(piece.getchannel('A'),dtype=np.float32)*np.array(mask)/255*mesh['alpha']
                piece.putalpha(Image.fromarray(alpha.astype('uint8')));frame.alpha_composite(piece,(left,top))
        frame.resize((256,288),Image.Resampling.LANCZOS).save(OUT/(str(portrait['skin_id'])+'.png'))
    sheet=Image.new('RGB',(6*200,6*250),'#f3eddf');draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
    for i,p in enumerate(portraits):
        im=Image.open(OUT/(str(p['skin_id'])+'.png'));im.thumbnail((180,205))
        x=i%6*200;y=i//6*250;sheet.paste(im,(x+(200-im.width)//2,y),im)
        draw.text((x+8,y+212),str(p['skin_id'])+' '+p['name'],font=font,fill='#302b24')
    sheet.save(OUT/'life-portraits-contact.jpg',quality=92)
    print(f'Rendered {len(portraits)} life-pose thumbnails')

if __name__=='__main__':main()
