import '@/styles/fonts'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@/styles/app.css'
import { loadConfig } from '@/lib/config'
import { initTheme } from '@/lib/theme'
import { initApi } from './adminApi'
import AdminApp from './AdminApp'

initTheme()
loadConfig().then(() => {
  initApi()
  createRoot(document.getElementById('root')!).render(<StrictMode><AdminApp /></StrictMode>)
})
