import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// DIAGNOSTIC: prove ANY network call works from the webview.
// Image src bypasses fetch + CORS entirely (resources load as "no-cors").
const ping = (tag) => { new Image().src = `http://127.0.0.1:8766/debug/level?tag=${tag}&t=${Date.now()}` }
try { ping('bundle-loaded') } catch (e) { /* ignore */ }
setInterval(() => { try { ping('heartbeat') } catch {} }, 5000)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
