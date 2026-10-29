(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  const defaultElevenVoice = 'JBFqnCBsd6RMkjVDRZzb';
  const providers = [
    ['gemini','Gemini','AI-written scripts','https://aistudio.google.com/apikey','✦'],
    ['elevenlabs','ElevenLabs','Natural voiceover and speech timing','https://elevenlabs.io/app/settings/api-keys','E'],
    ['pexels','Pexels','Stock videos and photos','https://www.pexels.com/api/','P'],
    ['pixabay','Pixabay','Alternative stock photos','https://pixabay.com/api/docs/','p'],
    ['huggingface','Hugging Face','Optional AI cover','https://huggingface.co/settings/tokens','H']
  ];
  const formats = {portrait:['9:16','PORTRAIT'],landscape:['16:9','LANDSCAPE'],square:['1:1','SQUARE']};
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
  const publicConfig=window.STUDIO_CONFIG||{};
  const defaultServer=publicConfig.backendUrl||(publicConfig.frontendOnly?'':location.origin);
  let state={server:defaultServer,token:'',keys:{},elevenlabsVoiceId:defaultElevenVoice}, projects=[], active='', videoUrl='', pollBusy=false;
  let session=0, librarySignature='', detailSignature='';
  const covers=new Map(), pendingCovers=new Map();
  $('server').value=defaultServer;
  $('keys').innerHTML=providers.map(([id,name,help,url,mark]) =>
    '<div class="provider-field" data-provider="'+id+'"><div class="provider-heading"><span class="provider-avatar" aria-hidden="true">'+mark+'</span><div><strong>'+name+'</strong><small>'+help+'</small></div><a href="'+url+'" target="_blank" rel="noreferrer noopener">Get key '+icon('arrow')+'</a></div><label for="key-'+id+'">'+name+' API key</label><div class="key-row"><input id="key-'+id+'" type="password" autocomplete="off" placeholder="Paste your '+name+' key"><button type="button" data-test="'+id+'" aria-label="Test '+name+' key">Test</button></div>'+
    (id==='elevenlabs'?'<label for="elevenlabs-voice">ElevenLabs Voice ID</label><input id="elevenlabs-voice" list="elevenlabs-voices" maxlength="80" autocomplete="off" value="'+defaultElevenVoice+'"><datalist id="elevenlabs-voices"></datalist><p class="field-note">Test your key to load your account’s voices, or paste a Voice ID. Your ElevenLabs plan and credits apply.</p>':'')+'</div>'
  ).join('');

  function notify(text,error=false){
    const message=$('message');message.textContent=text;message.className=error?'error':'ok';message.hidden=false;
    if(error&&message.getBoundingClientRect().bottom<0)message.scrollIntoView({behavior:reducedMotion.matches?'instant':'smooth',block:'center'});
  }
  function connection(connected){
    $('connection-pill').classList.toggle('connected',connected);
    $('connection-label').textContent=connected?'Server connected':'Not connected';
  }
  function tab(id){
    if(!['create','library','settings'].includes(id))return;
    document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==id);
    document.querySelectorAll('.nav-button').forEach(b=>{const current=b.dataset.tab===id;b.classList.toggle('on',current);if(current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});
    $('page-label').textContent={create:'Create',library:'My videos',settings:'Settings'}[id];
    window.scrollTo({top:0,behavior:'instant'});
    $('main').focus({preventScroll:true});
  }
  function serverOrigin(value){
    let url;try{url=new URL(value.trim())}catch{throw Error('Enter your rendering server’s HTTPS address in Settings.')}
    const local=['localhost','127.0.0.1'].includes(url.hostname);
    if(url.protocol!=='https:'&&!(url.protocol==='http:'&&local))throw Error('Use HTTPS for your rendering server.');
    if(url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw Error('Use only the server origin, without paths, credentials or query parameters.');
    return url.origin;
  }
  function draft(){return {server:serverOrigin($('server').value),token:$('token').value.trim(),keys:Object.fromEntries(providers.map(([id])=>[id,$('key-'+id).value.trim()])),elevenlabsVoiceId:$('elevenlabs-voice').value.trim()||defaultElevenVoice}}
  async function call(path,method='GET',body,settings=state,file=false){
    if(location.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(location.hostname))throw Error('Open your HTTPS app address before sending credentials.');
    if(!settings.token)throw Error('Add your server access token in Settings.');
    const origin=serverOrigin(settings.server);
    let response;
    try{response=await fetch(origin+path,{method,headers:{Authorization:'Bearer '+settings.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(file?120000:30000),cache:'no-store',redirect:'error'})}
    catch{connection(false);throw Error('Cannot connect. Check backend HTTPS, connection, and CORS permission for this app’s origin.')}
    if(!response.ok){if(response.status===401)connection(false);const data=await response.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:'Server returned HTTP '+response.status)}
    return file?response.blob():response.status===204?null:response.json();
  }
  async function run(button,action){
    button.disabled=true;button.setAttribute('aria-busy','true');
    try{await action()}catch(error){notify(error.message||'Request failed.',true)}
    finally{button.disabled=false;button.removeAttribute('aria-busy')}
  }
  function stopVideo(){if(videoUrl)URL.revokeObjectURL(videoUrl);videoUrl=''}
  function clearCovers(){session++;covers.forEach(url=>URL.revokeObjectURL(url));covers.clear();pendingCovers.clear();librarySignature='';detailSignature=''}
  function emptyState(connected){
    return '<div class="empty-state"><div class="empty-art">'+icon('video')+'</div><h3>'+(connected?'Your next story starts here.':'A home for your next big idea.')+'</h3><p>'+(connected?'Bring an idea to life. Your finished videos will be waiting here.':'Connect your server to see your private video library.')+'</p><button class="primary" data-tab="'+(connected?'create':'settings')+'">'+(connected?'Create your first video':'Connect server')+' '+icon('arrow')+'</button></div>';
  }
  async function cover(id,epoch){
    if(covers.has(id)||pendingCovers.has(id))return;
    pendingCovers.set(id,epoch);
    try{
      const blob=await call('/api/projects/'+encodeURIComponent(id)+'/files/thumbnail.jpg','GET',undefined,state,true);
      if(epoch!==session||!projects.some(p=>p.id===id))return;
      covers.set(id,URL.createObjectURL(blob));paintCovers();
    }catch{/* An unavailable cover must not block the project controls. */}
    finally{if(pendingCovers.get(id)===epoch)pendingCovers.delete(id)}
  }
  function paintCovers(){
    document.querySelectorAll('[data-cover]').forEach(image=>{
      const url=covers.get(image.dataset.cover);
      if(url&&image.getAttribute('src')!==url){image.src=url;image.closest('.project').classList.add('has-cover')}
    });
  }
  function renderLibrary(){
    const signature=JSON.stringify(projects.map(p=>[p.id,p.title,p.status,p.stage,p.duration,p.format,p.progress,p.updated]));
    if(signature!==librarySignature){
      librarySignature=signature;
      $('projects').innerHTML=projects.length?projects.map(p=>{
        const status=['queued','running','complete','failed'].includes(p.status)?p.status:'queued';
        const label={queued:'Queued',running:'Rendering',complete:'Ready to share',failed:'Needs attention'}[status];
        const aspect=(formats[p.format]||formats.portrait)[0];
        return '<button class="project" data-open="'+esc(p.id)+'"><div class="project-cover">'+(p.status==='complete'?'<img data-cover="'+esc(p.id)+'" alt="" loading="lazy">':'')+'<span class="project-format">'+aspect+'</span><span class="project-cover-title">'+esc(p.title)+'</span></div><div class="project-body"><strong>'+esc(p.title)+'</strong><div class="project-info"><span class="status-pill '+status+'">'+label+'</span><span>'+esc(p.duration)+'s · MP4</span></div>'+(['queued','running'].includes(p.status)?'<progress aria-label="Rendering progress" max="100" value="'+(Number(p.progress)||0)+'"></progress>':'')+'</div></button>';
      }).join(''):emptyState(Boolean(state.token));
    }
    $('library-count').textContent=state.token?String(projects.length):'—';
    paintCovers();
    projects.filter(p=>p.status==='complete').forEach(p=>cover(p.id,session));
    const selected=projects.find(p=>p.id===active);
    if(selected&&JSON.stringify(selected)!==detailSignature&&!$('detail').querySelector('video'))showDetail(selected);
  }
  async function load(){
    if(pollBusy)return;
    if(!state.token){librarySignature='';$('projects').innerHTML=emptyState(false);return}
    pollBusy=true;const epoch=session;
    try{const result=await call('/api/projects');if(epoch!==session)return;projects=result;connection(true);renderLibrary()}
    finally{pollBusy=false}
  }
  function showDetail(project){
    active=project.id;detailSignature=JSON.stringify(project);$('detail').hidden=false;
    const complete=project.status==='complete',failed=project.status==='failed';
    const credits=(project.credits||[]).map(c=>'<p>'+esc(c.provider)+' · '+esc(c.creator)+' '+(/^https:\/\/(www\.)?(pexels|pixabay)\.com\//.test(c.url)?'<a href="'+esc(c.url)+'" target="_blank" rel="noreferrer noopener">Source ↗</a>':'')+'</p>').join('');
    $('detail').innerHTML='<div class="row"><div><span class="detail-eyebrow">'+(complete?'YOUR FINISHED STORY':failed?'PROJECT DETAILS':'BRINGING YOUR IDEA TO LIFE')+'</span><h2>'+esc(project.title)+'</h2></div><button data-close="1">Close</button></div><div class="detail-stage">'+icon('clock')+esc(project.stage)+' · '+esc(project.duration)+'s</div>'+
      (project.error?'<p class="detail-error">'+esc(project.error)+'</p>':'')+
      (project.warnings||[]).map(w=>'<p class="detail-warning">'+esc(w)+'</p>').join('')+
      (complete?'<div id="player"></div><div class="detail-actions"><button class="primary" data-artifact="preview">'+icon('video')+' Preview</button><button data-artifact="video.mp4">'+icon('download')+' Download MP4</button><button data-artifact="thumbnail.jpg">'+icon('image')+' Cover</button>'+(project.caption_mode!=='none'?'<button data-artifact="captions.srt">'+icon('download')+' Captions</button>':'')+'</div>':
      !failed?'<div class="render-state"><div class="render-orb" aria-hidden="true"></div><div><strong>'+esc(project.stage)+'</strong><p>Your server is working on your video.</p><progress aria-label="Rendering progress" max="100" value="'+(Number(project.progress)||0)+'"></progress></div></div>':'')+
      (project.script?'<details><summary>Voiceover script</summary><pre>'+esc(project.script)+'</pre></details>':'')+
      '<details><summary>Media credits</summary>'+(credits||'<p>Studio backgrounds and your narration. No stock-media credits.</p>')+'</details>'+
      ((complete||failed)?'<button class="danger" data-delete="1">Delete project and files</button>':'');
  }
  function updatePreview(){
    const topic=$('topic').value.trim(),words=$('script').value.trim().split(/\s+/).filter(Boolean);
    const [aspect,name]=formats[$('format').value]||formats.portrait;
    $('topic-count').textContent=$('topic').value.length+' / 180';
    $('script-count').textContent=words.length+' words'+(words.length?' · ~'+Math.ceil(words.length/2.5)+'s narration':'');
    $('preview-title').textContent=topic?topic.slice(0,100):'A little idea.\nA whole new world.';
    $('preview-poster').dataset.format=$('format').value;
    $('preview-format-tag').textContent=aspect;
    $('preview-aspect').textContent=aspect+' · '+name;
    $('preview-length').textContent=$('duration').value+'s';
    $('preview-caption').hidden=$('captions').value==='none';
    $('preview-caption').textContent=words.length?words.slice(0,9).join(' '):'Your captions will appear here';
    $('recipe-voice').textContent=$('voice').selectedOptions[0].textContent.split('·')[0].trim();
    $('recipe-visuals').textContent=$('visuals').value==='studio'?'Studio backgrounds':'Stock media';
    $('recipe-duration').textContent=$('duration').value+' seconds';
  }
  function goToComposer(){
    $('create-form').scrollIntoView({behavior:reducedMotion.matches?'instant':'smooth',block:'start'});
    $('topic').focus({preventScroll:true});
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.tab){tab(button.dataset.tab);if(button.dataset.tab==='library')await run(button,load)}
    if(button.dataset.topic){$('topic').value=button.dataset.topic;updatePreview();$('topic').focus({preventScroll:true})}
    if(button.dataset.open){const project=projects.find(p=>p.id===button.dataset.open);if(project){stopVideo();showDetail(project);$('detail').scrollIntoView({behavior:reducedMotion.matches?'instant':'smooth',block:'start'})}}
    if(button.dataset.close){active='';detailSignature='';$('detail').hidden=true;stopVideo()}
    if(button.dataset.test)await run(button,async()=>{
      const settings=draft(),provider=button.dataset.test;if(!settings.keys[provider])throw Error('Enter this provider key first.');
      const result=await call('/api/verify-key','POST',{provider,key:settings.keys[provider]},settings);
      if(provider==='elevenlabs'&&Array.isArray(result.voices)){
        const voices=result.voices.filter(voice=>voice&&typeof voice.id==='string'&&/^[A-Za-z0-9_-]{1,80}$/.test(voice.id));
        $('elevenlabs-voices').innerHTML=voices.map(voice=>'<option value="'+esc(voice.id)+'">'+esc(voice.name)+'</option>').join('');
        if(voices.length&&$('elevenlabs-voice').value===defaultElevenVoice&&!voices.some(voice=>voice.id===defaultElevenVoice))$('elevenlabs-voice').value=voices[0].id;
        notify('ElevenLabs key accepted. '+voices.length+' voices loaded. Apply settings to use your selected Voice ID.');
      }else notify('Key accepted. Generation still depends on model access and quota.');
    });
    if(button.dataset.artifact)await run(button,async()=>{
      const id=active,preview=button.dataset.artifact==='preview',filename=preview?'video.mp4':button.dataset.artifact;
      const blob=await call('/api/projects/'+encodeURIComponent(id)+'/files/'+filename,'GET',undefined,state,true);
      const url=URL.createObjectURL(blob);
      if(preview){
        if(active!==id||!$('player')){URL.revokeObjectURL(url);return}
        stopVideo();videoUrl=url;const video=document.createElement('video');video.controls=true;video.playsInline=true;video.src=url;$('player').replaceChildren(video);video.play().catch(()=>{});
      }else{const link=document.createElement('a');link.href=url;link.download='faceless-'+filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000)}
    });
    if(button.dataset.delete&&confirm('Permanently delete this project and its files?'))await run(button,async()=>{
      const id=active;await call('/api/projects/'+encodeURIComponent(id),'DELETE');
      if(covers.has(id)){URL.revokeObjectURL(covers.get(id));covers.delete(id)}
      if(active===id){active='';detailSignature='';$('detail').hidden=true;stopVideo()}
      await load();notify('Project deleted.');
    });
  });
  $('settings-form').addEventListener('submit',event=>{
    event.preventDefault();try{const next=draft();if(!next.token)throw Error('Enter your server access token.');state=next;clearCovers();projects=[];active='';$('detail').hidden=true;stopVideo();
    notify('Settings applied for this page session.');load().catch(error=>notify(error.message,true));}catch(error){notify(error.message,true)}
  });
  $('connect').addEventListener('click',()=>run($('connect'),async()=>{
    const result=await call('/api/capabilities','GET',undefined,draft());
    if(!result.ffmpeg)throw Error('Connected, but FFmpeg is missing.');
    connection(true);notify('Connected · FFmpeg ready'+(result.local_voice?' · Local voice ready':'')+'. Apply settings to use this connection.');
  }));
  $('reveal').addEventListener('change',()=>['token',...providers.map(([id])=>'key-'+id)].forEach(id=>$(id).type=$('reveal').checked?'text':'password'));
  $('clear').addEventListener('click',()=>{
    state={server:defaultServer,token:'',keys:{},elevenlabsVoiceId:defaultElevenVoice};clearCovers();$('settings-form').reset();$('server').value=defaultServer;$('elevenlabs-voices').replaceChildren();
    ['token',...providers.map(([id])=>'key-'+id)].forEach(id=>{$(id).value='';$(id).type='password'});
    projects=[];active='';$('detail').hidden=true;stopVideo();connection(false);
    $('projects').innerHTML=emptyState(false);$('library-count').textContent='—';notify('Credentials cleared.');
  });
  $('refresh').addEventListener('click',()=>run($('refresh'),load));
  $('start-creating').addEventListener('click',goToComposer);
  $('sample-script').addEventListener('click',()=>{
    $('topic').value='The power of a small idea';
    $('script').value='Every great story starts with a small idea. Write it down. Explore a new angle. Take one small step today, and see where your curiosity leads.';
    $('voice').value='local-en';$('visuals').value='studio';$('duration').value='15';$('captions').value='script';$('thumbnail').checked=false;
    updatePreview();goToComposer();notify(state.token?'Sample story added. Your video is ready to create.':'Sample story added. Connect your server in Settings before creating.');
  });
  ['topic','script','format','duration','voice','visuals','captions'].forEach(id=>$(id).addEventListener('input',updatePreview));
  $('create-form').addEventListener('submit',event=>{
    event.preventDefault();
    if(!state.token){tab('settings');notify('Add your private server access token first.',true);$('token').focus();return}
    run($('generate'),async()=>{
      const body={topic:$('topic').value.trim(),script:$('script').value.trim(),duration:Number($('duration').value),format:$('format').value,voice:$('voice').value,elevenlabs_voice_id:state.elevenlabsVoiceId||defaultElevenVoice,visuals:$('visuals').value,captions:$('captions').value,thumbnail:$('thumbnail').checked,keys:state.keys};
      if(!body.script&&!state.keys.gemini)throw Error('Add a Gemini key in Settings or paste your own narration.');
      if(body.voice==='elevenlabs'&&!state.keys.elevenlabs)throw Error('Add your ElevenLabs API key in Settings.');
      if(body.voice==='elevenlabs'&&!/^[A-Za-z0-9_-]{1,80}$/.test(body.elevenlabs_voice_id))throw Error('Enter a valid ElevenLabs Voice ID in Settings.');
      if(body.visuals==='stock'&&!state.keys.pexels&&!state.keys.pixabay)throw Error('Add a stock key or choose Studio backgrounds.');
      if(body.thumbnail&&!state.keys.huggingface)throw Error('Add a Hugging Face token or disable AI cover.');
      const project=await call('/api/projects','POST',body);active=project.id;projects=[project,...projects.filter(p=>p.id!==project.id)];
      tab('library');renderLibrary();notify('Your video is rendering on your server.');
    });
  });
  document.querySelectorAll('[data-tilt]').forEach(element=>{
    let frame=0;
    const reset=()=>{cancelAnimationFrame(frame);element.style.removeProperty('--tilt-x');element.style.removeProperty('--tilt-y')};
    element.addEventListener('pointermove',event=>{
      if(reducedMotion.matches||!finePointer.matches||event.pointerType!=='mouse')return;
      const bounds=element.parentElement.getBoundingClientRect();
      const x=Math.max(-1,Math.min(1,(event.clientX-bounds.left)/bounds.width*2-1));
      const y=Math.max(-1,Math.min(1,(event.clientY-bounds.top)/bounds.height*2-1));
      cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{element.style.setProperty('--tilt-x',(-y*4).toFixed(2)+'deg');element.style.setProperty('--tilt-y',(x*7).toFixed(2)+'deg')});
    });
    element.addEventListener('pointerleave',reset);element.addEventListener('pointercancel',reset);
    reducedMotion.addEventListener('change',reset);
  });
  updatePreview();
  setInterval(()=>{if(!document.hidden)load().catch(()=>{})},4000);
})();
