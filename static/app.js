const fmt = value => value ? new Date(value).toLocaleString('pt-BR') : '-';
async function getJson(url){ const r = await fetch(url); if(!r.ok){ location.href='/login'; throw new Error('Erro'); } return r.json(); }
async function loadSummary(){
  const data = await getJson('/api/summary');
  document.getElementById('m-total').textContent = data.total || 0;
  document.getElementById('m-unique').textContent = data.unique_total || 0;
  document.getElementById('m-locations').textContent = (data.locations || []).length;
  document.getElementById('m-last').textContent = fmt(data.last_record);
  const select = document.getElementById('location');
  const current = select.value;
  select.innerHTML = '<option value="">Todos os locais</option>' + (data.locations || []).map(l => `<option value="${l.code}">${l.name} (${l.total_imeis})</option>`).join('');
  select.value = current;
}
function render(records){
  document.getElementById('count').textContent = `${records.length} itens`;
  const rows = document.getElementById('rows');
  if(!records.length){ rows.innerHTML = '<tr><td colspan="5" class="empty">Nenhum IMEI encontrado.</td></tr>'; return; }
  rows.innerHTML = records.map(r => `<tr><td class="mono">${r.imei}</td><td><span class="badge">${r.location_name}</span></td><td>${r.file_name || '-'}</td><td class="muted">${fmt(r.updated_at)}</td><td class="muted">${r.location_folder || r.file_path || '-'}</td></tr>`).join('');
}
async function search(){
  const params = new URLSearchParams();
  const imei = document.getElementById('imei').value.replace(/\D/g, '');
  const location = document.getElementById('location').value;
  if(imei) params.set('imei', imei);
  if(location) params.set('location', location);
  const data = await getJson('/api/imeis?' + params.toString());
  render(data.records || []);
}
document.getElementById('imei').addEventListener('keydown', e => { if(e.key === 'Enter') search(); });
document.getElementById('location').addEventListener('change', search);
loadSummary().then(search);
