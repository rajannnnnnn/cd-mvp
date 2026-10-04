import '@/styles/fonts'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/app.css'
import { loadConfig } from './lib/config'
import { initApi } from './api/client'
import { initTheme } from './lib/theme'
import App from './app/App'

initTheme()
loadConfig().then(() => {
  initApi()
  createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
})
