import { createContext, Suspense, useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { cx } from '@/lib/format'
import { useT } from '@/i18n'
import { CHAT_TABS, TabBar } from './SectionTabs'
import { ChatsBodySkeleton, PipelineBodySkeleton } from '@/ui/skeletons'

/** Where the Pipeline screen puts its own bits (the count, the practice button) in the shared top bar. */
export const TabSlot = createContext<HTMLElement | null>(null)

/** Conversations and Pipeline are two views of one section. This layout owns the top bar (title and toggle), which therefore never
 *  unmounts: the highlight slides from one tab to the other, the title changes in place and only the content below it changes. */
export default function ChatsLayout() {
  const { t } = useT()
  const { pathname } = useLocation()
  const pipeline = pathname.startsWith('/pipeline')
  const threadOpen = /^\/chats\/[^/]+/.test(pathname)
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  const was = useRef(pipeline)
  const switching = was.current !== pipeline          // true only on the render that follows a tap on the other tab
  useEffect(() => { was.current = pipeline }, [pipeline])
  const fade = switching ? 'animate-fadeUp' : ''
  return (
    <div className="flex h-[calc(100dvh-57px-76px)] flex-col lg:h-[calc(100dvh-57px)]">
      <TabBar tabs={CHAT_TABS()} className={threadOpen ? 'hidden lg:flex' : undefined}
        left={<><h1 key={pipeline ? 'p' : 'c'} className={cx('truncate font-display text-xl font-extrabold', fade)}>{pipeline ? t('nav.pipeline', 'Pipeline') : t('chat.title', 'Conversations')}</h1><span ref={setSlot} className="flex items-center gap-3" /></>} />
      <div key={pipeline ? 'pipeline' : 'chats'} className={cx('flex min-h-0 flex-1 flex-col', fade)}>
        <Suspense fallback={pipeline ? <PipelineBodySkeleton /> : <ChatsBodySkeleton thread={threadOpen} />}>
          <TabSlot.Provider value={slot}><Outlet /></TabSlot.Provider>
        </Suspense>
      </div>
    </div>
  )
}
