const API = '/api';
const token = () => localStorage.getItem('token') || '';
const headers = () => ({'Content-Type':'application/json',Authorization:'Bearer '+token()});
const api = {get:url=>fetch(API+url,{headers:headers()}).then(r=>r.json().then(d=>({status:r.status,data:d}))),
post:(url,body)=>fetch(API+url,{method:'POST',headers:headers(),body:JSON.stringify(body)}).then(r=>r.json().then(d=>({status:r.status,data:d}))),
put:(url,body)=>fetch(API+url,{method:'PUT',headers:headers(),body:JSON.stringify(body)}).then(r=>r.json().then(d=>({status:r.status,data:d}))),
del:url=>fetch(API+url,{method:'DELETE',headers:headers()}).then(r=>r.json().then(d=>({status:r.status,data:d})))};