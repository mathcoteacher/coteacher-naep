/// <reference types="@cloudflare/workers-types" />

export interface Env {
  ASSETS: Fetcher; // static assets binding (public/)
}

/** ===== Types for naep.json ===== */
type RatioText = `${number} out of ${number}`;
interface NaepValue {
  text: RatioText;
  ratio?: number;
  numerator?: number;
  denominator?: number;
}
interface NaepData {
  national: { US: NaepValue };
  states: Record<string, NaepValue>;
}

/** ===== In-memory cache for naep.json ===== */
let NAEP_DATA_PROMISE: Promise<NaepData> | null = null;

/** ===== USPS -> Full state name map ===== */
const STATE_NAME: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia",
  FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana",
  ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota",
  MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada",
  NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
  OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina",
  SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont",
  VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming"
};

/** ===== Utility: strict-validate the JSON we load ===== */
const TEXT_RE = /^\d+ out of \d+$/;
function isValidValue(v: any): v is NaepValue {
  return v && typeof v.text === "string" && TEXT_RE.test(v.text);
}
function validateData(d: any): d is NaepData {
  if (!d || typeof d !== "object") return false;
  if (!d.national || !d.national.US || !isValidValue(d.national.US)) return false;
  if (!d.states || typeof d.states !== "object") return false;
  for (const [k, v] of Object.entries(d.states)) {
    if (typeof k !== "string" || k.length !== 2) return false;
    if (!isValidValue(v)) return false;
  }
  return true;
}

/** ===== Load naep.json from the static Assets binding ===== */
async function loadNaep(env: Env): Promise<NaepData> {
  if (!NAEP_DATA_PROMISE) {
    NAEP_DATA_PROMISE = (async () => {
      const res = await env.ASSETS.fetch("https://assets.local/naep.json");
      if (!res.ok) throw new Error(`Failed to load naep.json: ${res.status}`);
      const json = await res.json();
      if (!validateData(json)) throw new Error("naep.json failed schema validation");
      return json as NaepData;
    })();
  }
  return NAEP_DATA_PROMISE;
}

/** ===== Resolve region from Cloudflare request.cf ===== */
function resolveRegion(req: Request): { country: string | null; stateCode: string | null } {
  const cf = (req as any).cf || {};
  const country = typeof cf.country === "string" ? (cf.country as string) : null;
  const stateCode = typeof cf.regionCode === "string" ? (cf.regionCode as string) : null;
  return { country, stateCode };
}

/** ===== Choose the NAEP record ===== */
function chooseNaepRecord(data: NaepData, country: string | null, stateCode: string | null) {
  if (country === "US" && stateCode && data.states[stateCode]) {
    const name = STATE_NAME[stateCode] ?? stateCode;
    return { scopeLabel: name, value: data.states[stateCode], isNational: false };
  }
  return { scopeLabel: "U.S.", value: data.national.US, isNational: true };
}

/** ===== Parse numerator/denominator from NaepValue ===== */
function parseRatio(value: NaepValue): { numerator: number; denominator: number } {
  if (value.numerator != null && value.denominator != null) {
    return { numerator: value.numerator, denominator: value.denominator };
  }
  const m = value.text.match(/^(\d+)\s+out\s+of\s+(\d+)$/);
  return { numerator: Number(m![1]), denominator: Number(m![2]) };
}

/** ===== Escape utility ===== */
function escapeHtml(s: string) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** ===== HTML templates (Ember theme) ===== */

function baseHeaders(extra?: Record<string, string>): Headers {
  return new Headers({
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy":
      "default-src 'none'; script-src 'unsafe-inline'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    ...(extra || {})
  });
}

function layoutHTML(title: string, body: string, opts?: { noindex?: boolean }) {
  const metaRobots = opts?.noindex ? `<meta name="robots" content="noindex" />` : "";
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>${escapeHtml(title)}</title>
${metaRobots}
<style>
  :root{
    --page-bg:#f5f0e8; --card-bg:#faf7f2; --ink:#2b2b2b;
    --muted:#888; --accent:#e8542f;
    --maxw: 64rem;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --page-bg:#1e1b17; --card-bg:#2a2621; --ink:#e0dbd2;
      --muted:#7a756c; --accent:#e8542f;
    }
  }
  html{scroll-behavior:smooth}
  body{
    margin:0; background:var(--page-bg);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
    -webkit-font-smoothing:antialiased;
    font-variant-numeric: lining-nums tabular-nums;
    color:var(--ink);
  }
  .wrap{max-width:var(--maxw); margin:0 auto; padding: clamp(1rem, 4vw, 2.5rem) clamp(1rem, 3.5vw, 1.5rem); display:grid; gap:clamp(1rem, 2vw, 1.5rem)}
  .card{
    background:var(--card-bg);
    border-radius:18px;
    padding:clamp(1.25rem, 3.2vw, 2rem);
    box-shadow: 0 12px 28px rgba(44,30,10,.08), 0 2px 8px rgba(44,30,10,.06);
  }

  /* Hero animation section */
  .hero-anim{text-align:center; padding-top:clamp(0.5rem, 2vw, 1.5rem)}
  .hero-anim canvas{max-width:100%; display:block; margin:0 auto}
  .message{
    margin:0.4rem auto 0; text-align:center; max-width:600px;
    opacity:0; transition:opacity 0.8s;
  }
  .message.visible{opacity:1}
  .headline{
    font-size:clamp(1.5rem, 5vw, 2.4rem); font-weight:800;
    line-height:1.15; letter-spacing:-0.02em;
  }
  .headline .num{color:var(--accent); font-weight:900}
  .subline{
    margin-top:0.6rem; font-size:clamp(0.85rem, 2.5vw, 1.05rem);
    color:var(--muted); line-height:1.5;
  }
  .subsubline{
    margin-top:0.5rem; font-size:clamp(0.85rem, 2.5vw, 1.05rem);
    color:var(--accent); line-height:1.5; font-weight:600;
  }

  /* Legacy text styles (investor/error pages) */
  .hero{display:block}
  h1.hero-line{
    margin:0; line-height:1.1; font-weight:800;
    font-size: clamp(1.75rem, 4.2vw + .5rem, 3.25rem);
    letter-spacing:.002em; text-wrap: balance; text-align:left;
  }
  .small{font-size:.9em; opacity:.9}
  .lede{margin:.5rem 0 0; font-size:clamp(1.05rem, 1.1vw + .7rem, 1.25rem); opacity:.9}
  .note{margin-top:.35rem; color:var(--muted); font-size:.98rem}

  /* Info card headings */
  .h2{margin:0 0 .35rem; font-weight:800; font-size:clamp(1.1rem, 1.4vw + .7rem, 1.5rem)}
  .body{font-size:clamp(1rem, 1vw + .6rem, 1.1rem)}

  /* Utilities */
  .stack{display:grid; gap:clamp(.9rem, 1.6vw, 1.25rem)}
  a.btn{display:inline-block; margin-top:.9rem; padding:.7rem 1rem; border-radius:.75rem; border:1px solid rgba(232,84,47,.3); text-decoration:none; color:var(--accent); font-weight:700}
  a.btn:hover{border-color:var(--accent)}
  a.btn:focus{outline:3px solid var(--accent); outline-offset:2px}
</style>
</head>
<body>
${body}
</body>
</html>`;
}

/** ===== Ember Flicker animation script (inline JS) ===== */
function buildAnimationScript(numerator: number, denominator: number): string {
  return `(function(){
var ACCENT='#e8542f',DEAD_LIGHT='#c5bfb5',DEAD_DARK='#3a3632',BG_LIGHT='#f5f0e8',BG_DARK='#1e1b17';
var TOTAL=${denominator},FLICKER_COUNT=${numerator};
var isDark=window.matchMedia('(prefers-color-scheme:dark)').matches;
var canvas=document.getElementById('c'),ctx=canvas.getContext('2d'),msg=document.getElementById('msg');
var W,H,FW,FH,GAP,SX,SY;
function sizing(){
var maxW=Math.min(window.innerWidth-40,700);
W=maxW;FW=Math.round(W*0.55/TOTAL);FH=Math.round(FW*2.6);
H=Math.round(Math.max(maxW*0.38,FH*1.4));
canvas.width=W;canvas.height=H;
GAP=Math.round((W-FW*TOTAL)/(TOTAL+1));SX=GAP;
SY=Math.round((H-FH)/2)+10;
}
function drawFigure(x,y,w,h,color,alpha){
ctx.save();ctx.globalAlpha=alpha;ctx.fillStyle=color;
var headR=w*0.42,cx=x+w/2,cy=y+headR;
ctx.beginPath();ctx.arc(cx,cy,headR,0,Math.PI*2);ctx.fill();
var bt=cy+headR+h*0.04,bb=y+h,bw=w*0.85,bx=x+(w-bw)/2;
ctx.beginPath();
ctx.moveTo(bx+bw*0.15,bt);ctx.lineTo(bx+bw*0.85,bt);
ctx.quadraticCurveTo(bx+bw,bt,bx+bw,bt+(bb-bt)*0.15);
ctx.lineTo(bx+bw,bb-6);ctx.quadraticCurveTo(bx+bw,bb,bx+bw-6,bb);
ctx.lineTo(bx+6,bb);ctx.quadraticCurveTo(bx,bb,bx,bb-6);
ctx.lineTo(bx,bt+(bb-bt)*0.15);ctx.quadraticCurveTo(bx,bt,bx+bw*0.15,bt);
ctx.fill();ctx.restore();
}
function drawGlow(x,y,w,h,color,alpha,glowR){
if(glowR>0&&alpha>0.1){ctx.save();ctx.globalAlpha=alpha*0.35;ctx.shadowColor=color;ctx.shadowBlur=glowR;drawFigure(x,y,w,h,color,1);ctx.restore();}
drawFigure(x,y,w,h,color,alpha);
}
function buildPixels(fi){
var fx=SX+fi*(FW+GAP),pSize=Math.max(2,FW*0.12);
var cols=Math.ceil(FW/pSize),rows=Math.ceil(FH/pSize);
var off=document.createElement('canvas');off.width=FW+4;off.height=FH+4;
var oc=off.getContext('2d');oc.fillStyle=ACCENT;
var headR=FW*0.42,cx=2+FW/2,cy=2+headR;
oc.beginPath();oc.arc(cx,cy,headR,0,Math.PI*2);oc.fill();
var bt=cy+headR+FH*0.04,bb=2+FH,bw=FW*0.85,bx=2+(FW-bw)/2;
oc.beginPath();
oc.moveTo(bx+bw*0.15,bt);oc.lineTo(bx+bw*0.85,bt);
oc.quadraticCurveTo(bx+bw,bt,bx+bw,bt+(bb-bt)*0.15);
oc.lineTo(bx+bw,bb-6);oc.quadraticCurveTo(bx+bw,bb,bx+bw-6,bb);
oc.lineTo(bx+6,bb);oc.quadraticCurveTo(bx,bb,bx,bb-6);
oc.lineTo(bx,bt+(bb-bt)*0.15);oc.quadraticCurveTo(bx,bt,bx+bw*0.15,bt);
oc.fill();
var imgD=oc.getImageData(0,0,off.width,off.height),pixels=[];
for(var r=0;r<rows;r++){for(var c=0;c<cols;c++){
var px=Math.round(c*pSize+pSize/2)+2,py=Math.round(r*pSize+pSize/2)+2;
if(px<imgD.width&&py<imgD.height&&imgD.data[(py*imgD.width+px)*4+3]>128){
pixels.push({tx:fx+c*pSize,ty:SY+r*pSize,x:fx+c*pSize+(Math.random()-0.5)*W*0.5,y:SY+r*pSize+(Math.random()-0.5)*H*0.8,size:pSize});
}}}
return pixels;
}
var flickerOrder=(function(){var a=[];for(var i=0;i<FLICKER_COUNT;i++)a.push(i);for(var i=a.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1)),t=a[i];a[i]=a[j];a[j]=t;}return a;})();
var flickerDuration=0.25,flickerGap=Math.min(0.22,1.5/Math.max(FLICKER_COUNT-1,1));
var formingPixels=[],startTime=0,animFrame,textShown=false;
function ease(t){return t<0.5?2*t*t:1-Math.pow(-2*t+2,2)/2;}
function init(){
sizing();msg.classList.remove('visible');msg.style.opacity='0';textShown=false;
formingPixels=[];
for(var i=0;i<TOTAL;i++)formingPixels.push(buildPixels(i));
startTime=performance.now();
if(animFrame)cancelAnimationFrame(animFrame);animate();
}
function animate(){
var elapsed=(performance.now()-startTime)/1000;
var bg=isDark?BG_DARK:BG_LIGHT;
ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
if(elapsed<1.5){
var t=ease(Math.min(elapsed/1.5,1));
for(var i=0;i<TOTAL;i++){for(var j=0;j<formingPixels[i].length;j++){var p=formingPixels[i][j];
ctx.globalAlpha=0.3+0.7*t;ctx.fillStyle=ACCENT;
ctx.fillRect(p.x+(p.tx-p.x)*t,p.y+(p.ty-p.y)*t,p.size,p.size);
}}ctx.globalAlpha=1;
}else if(elapsed<2.0){
for(var i=0;i<TOTAL;i++){var pulse=0.92+0.08*Math.sin(elapsed*3+i*0.7);drawGlow(SX+i*(FW+GAP),SY,FW,FH,ACCENT,pulse,10);}
}else if(elapsed<4.0){
var dt=elapsed-2.0,fs=[];for(var i=0;i<TOTAL;i++)fs.push(1);
for(var k=0;k<flickerOrder.length;k++){
var fi=flickerOrder[k],lt=(dt-k*flickerGap)/flickerDuration;
if(lt>=0&&lt<1)fs[fi]=Math.sin(lt*Math.PI*8)*0.5*(1-ease(lt))+0.5*(1-ease(lt));
else if(lt>=1)fs[fi]=0;
}
for(var i=0;i<TOTAL;i++){var fx=SX+i*(FW+GAP);
if(i>=FLICKER_COUNT){drawGlow(fx,SY,FW,FH,ACCENT,0.92+0.08*Math.sin(elapsed*3+i*0.7),10);}
else{var s=fs[i];
if(s<=0)drawFigure(fx,SY,FW,FH,isDark?DEAD_DARK:DEAD_LIGHT,0.5);
else if(s>=0.95)drawGlow(fx,SY,FW,FH,ACCENT,0.95,10);
else drawGlow(fx,SY,FW,FH,ACCENT,s,s*14);
}}
}else{
for(var i=0;i<FLICKER_COUNT;i++)drawFigure(SX+i*(FW+GAP),SY,FW,FH,isDark?DEAD_DARK:DEAD_LIGHT,0.5);
for(var i=FLICKER_COUNT;i<TOTAL;i++)drawGlow(SX+i*(FW+GAP),SY,FW,FH,ACCENT,0.92+0.08*Math.sin(elapsed*3+i*0.7),10);
if(elapsed>4.3&&!textShown){textShown=true;msg.style.opacity='';void msg.offsetHeight;msg.classList.add('visible');}
}
animFrame=requestAnimationFrame(animate);
}
window.addEventListener('resize',init);
init();
})();`;
}

function homeHTML(numerator: number, denominator: number, label: string) {
  const numStr = escapeHtml(String(numerator));
  const denStr = escapeHtml(String(denominator));
  const labelStr = escapeHtml(label);
  const title = `${numerator} out of ${denominator} ${label} 8th graders are below proficient in math.`;
  const body = `<main class="wrap" role="main">
  <section class="hero-anim">
    <canvas id="c"></canvas>
    <div class="message" id="msg">
      <div class="headline"><span class="num">${numStr}</span> out of <span class="num">${denStr}</span> ${labelStr} 8th graders are below proficient in math.</div>
      <div class="subline">They are deep down a road of a lifetime of lost potential.</div>
      <div class="subsubline">It doesn't have to be this way.</div>
    </div>
    <a class="btn" href="#how">See How</a>
    <p id="ip-note" class="note">Detected via IP.</p>
  </section>

  <section id="how" class="card stack">
    <div class="h2">How this number is chosen</div>
    <div class="body">We detect your location via IP at the moment of request and select the corresponding state's share of 8th graders below proficient in mathematics from a bundled NAEP dataset. If a state cannot be determined, we show the U.S. national number.</div>
    <div class="body">This site is static, makes no external calls, and uses a single JSON file built into the deployment. No cookies or identifiers are stored.</div>
  </section>
</main>
<script>${buildAnimationScript(numerator, denominator)}</script>`;
  return layoutHTML(title, body, { noindex: false });
}

function investorHTML() {
  const title = "Math CoTeacher — Investor Notes";
  const body = `<main class="wrap" role="main">
  <section class="card hero">
    <h1 class="hero-line">Investor Overview</h1>
    <p class="lede">This page is intentionally not linked from the homepage.</p>
  </section>
  <section class="card stack">
    <div class="h2">Why this matters</div>
    <div class="body">Large-scale proficiency gaps persist across states; scalable interventions and teacher-augmentation unlock step-change outcomes.</div>
    <div class="h2" style="margin-top: .75rem;">Approach</div>
    <div class="body">Zero-friction awareness, clear framing of the problem, and a credible path to intervention across school systems.</div>
  </section>
</main>`;
  return layoutHTML(title, body, { noindex: true });
}

function notFoundHTML() {
  const body = `<main class="wrap" role="main">
  <section class="card hero"><h1 class="hero-line">Not Found</h1><p class="lede">The page you requested does not exist.</p></section>
</main>`;
  return layoutHTML("Not Found", body, { noindex: false });
}

/** ===== Worker entry ===== */
export default {
  async fetch(request, env): Promise<Response> {
    try {
      const url = new URL(request.url);

      if (request.method !== "GET") {
        return new Response("Method Not Allowed", {
          status: 405,
          headers: baseHeaders()
        });
      }

      if (url.pathname === "/investor") {
        return new Response(investorHTML(), {
          status: 200,
          headers: baseHeaders({ "X-Robots-Tag": "noindex" })
        });
      }

      if (url.pathname === "/") {
        const data = await loadNaep(env as Env);
        const { country, stateCode } = resolveRegion(request);
        const { scopeLabel, value } = chooseNaepRecord(data, country, stateCode);
        const { numerator, denominator } = parseRatio(value);

        const html = homeHTML(numerator, denominator, scopeLabel);
        return new Response(html, { status: 200, headers: baseHeaders() });
      }

      return new Response(notFoundHTML(), { status: 404, headers: baseHeaders() });
    } catch (err) {
      console.error(err);
      const body = `<main class="wrap" role="main">
        <section class="card hero">
          <h1 class="hero-line">Temporary Error</h1>
          <p class="lede">Falling back to the U.S. number.</p>
        </section>
      </main>`;
      return new Response(layoutHTML("Temporary Error", body), {
        status: 200,
        headers: baseHeaders()
      });
    }
  }
} satisfies ExportedHandler<Env>;
