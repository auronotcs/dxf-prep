const fs=require('fs'),path=require('path');
const sharp=require('C:/Users/auron/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const root=__dirname;
const text=(x,y,s,z=16)=>`<text x="${x}" y="${y}" font-family="Segoe UI,Arial" font-size="${z}" fill="#334454">${s}</text>`;
let body='<rect width="1080" height="450" fill="#F0F4F7"/>'+text(22,32,'CANVAS LINE WEIGHT / DISCUSSION ONLY',22)+text(22,59,'Same vector geometry. Left uses current widths; right illustrates a thinner display.',15);
for(const [i,label,width,sel] of [[0,'Current: 2 px / selected 3 px',2,3],[1,'Proposed: 1 px / selected 2 px',1,2]]){
let x=20+i*530;body+=`<rect x="${x}" y="80" width="510" height="335" rx="6" fill="white"/>`+text(x+14,108,label,18);
for(let j=0;j<9;j++)body+=`<path d="M${x+15+j*55} 125V375 M${x+15} ${130+j*28}H${x+495}" stroke="#EAF0F4" stroke-width="1"/>`;
body+=`<g transform="translate(${x+35} 140)"><path d="M0 210L78 0H117L195 210H153L134 155H61L42 210Z M74 118H121L97 47Z" fill="none" stroke="#148ACE" stroke-width="${width}"/><circle cx="299" cy="69" r="58" fill="none" stroke="#148ACE" stroke-width="${width}"/><path d="M230 185C265 103 307 230 358 158L427 200" fill="none" stroke="#E69023" stroke-width="${sel}"/></g>`;
body+=text(x+18,400,'Blue: geometry     Orange: selected geometry',13);
}
body+=text(22,439,'Illustration, not a Workbench screenshot. Actual Tk canvas edge rendering must be checked separately.',12);
(async()=>{const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="450">${body}</svg>`;fs.writeFileSync(path.join(root,'line-weight-study.svg'),svg);await sharp(Buffer.from(svg)).png().toFile(path.join(root,'line-weight-study.png'));})();
