import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { cx } from '@/lib/format'
import { STAGES } from '@/lib/stages'
import { Segmented } from '@/ui'
import { CHAT_TABS, INSIGHT_TABS, SectionTabs, TabBar } from '@/app/SectionTabs'

/* ------------------------------------------------------------------------------------------------------------------
 * Loading skeletons.
 *
 * Rule: a screen never blanks out. The app frame (side menu, top bar, bottom bar) stays on screen, a screen draws its
 * own title and fixed parts at once, and only the parts that wait for data or code are drawn as grey placeholders
 * shaped like the real thing.
 *
 * Keep these in step with the screens. When you change the layout of a screen, change its skeleton in this file:
 *   - the PARTS below (CardGridSkeleton, TableSkeleton, ...) are used by the screens while their data loads;
 *   - PAGE_SKELETONS has one entry per route and is shown while that screen's code loads.
 * `skeletons.test.ts` fails when a route has no entry here.
 * ---------------------------------------------------------------------------------------------------------------- */

const Bone = ({ className }: { className?: string }) => <div className={cx('skeleton', className)} />
const range = (n: number) => Array.from({ length: n }, (_, i) => i)

/* ------------------------------------------------------------------ parts (used by the screens while data loads) */

/** A row of figure tiles (Home, Analytics). */
export function KpiRowSkeleton({ count = 6, className }: { count?: number; className?: string }) {
  return <div className={cx('grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6', className)} aria-hidden>{range(count).map((i) => <Bone key={i} className="h-[104px] rounded-2xl" />)}</div>
}

/** Product and offer cards in their grid: the same sizes as the real cards, so nothing jumps when data arrives. */
export function CardGridSkeleton({ count = 6, height = 200 }: { count?: number; height?: number }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-hidden>
      {range(count).map((i) => (
        <div key={i} className="card card-pad flex flex-col" style={{ height }}>
          <div className="flex items-start gap-3"><Bone className="h-9 w-9 shrink-0 !rounded-xl" /><div className="flex-1 space-y-2"><Bone className="h-4 w-3/5" /><Bone className="h-3 w-2/5" /></div></div>
          <Bone className="mt-5 h-6 w-1/3" />
          <div className="mt-4 flex gap-2"><Bone className="h-5 w-20 !rounded-full" /><Bone className="h-5 w-24 !rounded-full" /></div>
        </div>))}
    </div>
  )
}

/** Avatar, two lines of text and a time: chat lists, latest chats, customer lists on a phone. */
export function ListRowsSkeleton({ count = 6, className }: { count?: number; className?: string }) {
  return (
    <div className={className} aria-hidden>
      {range(count).map((i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3"><Bone className="h-11 w-11 shrink-0 !rounded-full" /><div className="flex-1 space-y-2"><Bone className="h-4 w-1/2" /><Bone className="h-3 w-4/5" /></div><Bone className="h-3 w-8" /></div>))}
    </div>
  )
}

/** A table inside a card: a header line and rows. */
export function TableSkeleton({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="card overflow-hidden" aria-hidden>
      <div className="flex gap-4 border-b border-line/60 px-4 py-3">{range(cols).map((c) => <Bone key={c} className="h-3 flex-1" />)}</div>
      {range(rows).map((r) => (
        <div key={r} className="flex items-center gap-4 border-b border-line/40 px-4 py-3.5 last:border-0">{range(cols).map((c) => <Bone key={c} className={cx('h-4 flex-1', c === 0 && 'max-w-[40%]')} />)}</div>))}
    </div>
  )
}

/** Stacked cards: inbox items, tickets, settings sections. */
export function CardListSkeleton({ count = 3, height = 96 }: { count?: number; height?: number }) {
  return (
    <div className="space-y-3" aria-hidden>
      {range(count).map((i) => (
        <div key={i} className="card card-pad" style={{ minHeight: height }}>
          <div className="flex items-center gap-3"><Bone className="h-10 w-10 shrink-0 !rounded-full" /><div className="flex-1 space-y-2"><Bone className="h-4 w-2/5" /><Bone className="h-3 w-3/4" /></div></div>
          <div className="mt-4 flex gap-2"><Bone className="h-8 w-24" /><Bone className="h-8 w-20" /></div>
        </div>))}
    </div>
  )
}

/** A titled panel with a chart or text block. */
export function PanelSkeleton({ height = 200, className }: { height?: number; className?: string }) {
  return <div className={cx('card card-pad', className)} aria-hidden><Bone className="h-5 w-32" /><div style={{ height }} className="skeleton mt-4 w-full rounded-xl" /></div>
}

/** The pipeline board's columns. */
export function ColumnsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="-mx-4 flex min-h-0 flex-1 gap-4 overflow-hidden px-4 pb-4 lg:-mx-8 lg:px-8" aria-hidden>
      {STAGES.slice(0, count).map((s) => (
        <div key={s} className="flex w-[272px] shrink-0 flex-col gap-2.5 rounded-2xl bg-surface2/70 p-2.5">
          <div className="flex items-center justify-between px-2 pb-1 pt-1"><Bone className="h-4 w-24" /><Bone className="h-5 w-7 !rounded-full" /></div>
          {range(3).map((i) => <div key={i} className="rounded-xl border border-line/70 bg-surface p-3"><div className="flex items-center gap-2.5"><Bone className="h-8 w-8 !rounded-full" /><div className="flex-1 space-y-1.5"><Bone className="h-3.5 w-3/5" /><Bone className="h-3 w-1/3" /></div></div><Bone className="mt-3 h-3 w-full" /><Bone className="mt-1.5 h-3 w-4/5" /></div>)}
        </div>))}
    </div>
  )
}

/** Message bubbles in a conversation. */
export function ThreadSkeleton() {
  return (
    <div className="flex h-full flex-col" aria-hidden>
      <div className="flex items-center gap-3 border-b border-line/70 px-4 py-3"><Bone className="h-10 w-10 !rounded-full" /><div className="flex-1 space-y-2"><Bone className="h-4 w-1/3" /><Bone className="h-3 w-1/4" /></div></div>
      <div className="flex flex-1 flex-col gap-3 p-4">
        <Bone className="h-12 w-3/5 !rounded-2xl" /><Bone className="ml-auto h-16 w-2/3 !rounded-2xl" /><Bone className="h-10 w-1/2 !rounded-2xl" /><Bone className="ml-auto h-12 w-3/5 !rounded-2xl" />
      </div>
      <div className="border-t border-line/70 p-3"><Bone className="h-11 w-full !rounded-xl" /></div>
    </div>
  )
}

/** A form: label and field pairs. */
export function FormSkeleton({ fields = 4 }: { fields?: number }) {
  return <div className="space-y-5" aria-hidden>{range(fields).map((i) => <div key={i} className="space-y-2"><Bone className="h-3.5 w-28" /><Bone className="h-11 w-full !rounded-xl" /></div>)}</div>
}

/* ------------------------------------------------------------------ page chrome pieces */

/** The page's real title and subtitle: they never depend on data, so they are never drawn as placeholders. */
const TitleBar = ({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) => (
  <div className="flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-[28px] font-extrabold">{title}</h1>{sub && <p className="text-[15px] text-muted">{sub}</p>}</div>{children}</div>
)
/** A tab strip with its real labels (nothing in it waits for data). */
const StaticTabs = ({ labels }: { labels: string[] }) => <Segmented value={labels[0]} onChange={() => {}} options={labels.map((l) => ({ value: l, label: l }))} />
const Page = ({ children, wide = false }: { children: ReactNode; wide?: boolean }) => (
  <div className={cx('mx-auto space-y-5 px-4 py-6 lg:px-8 lg:py-8', wide ? 'max-w-[1560px]' : 'max-w-[1180px]')} aria-busy="true" aria-label="Loading">{children}</div>
)
/** View toggles on the left, search and the New button on the right (Products, Offers). */
const Toolbar = () => <div className="flex flex-wrap items-center justify-between gap-3"><div className="order-2 flex gap-2 sm:order-1"><Bone className="h-10 w-24 !rounded-xl" /><Bone className="h-10 w-40 !rounded-xl" /></div><div className="order-1 flex w-full items-center gap-2 sm:order-2 sm:w-auto"><Bone className="h-11 flex-1 !rounded-xl sm:w-72 sm:flex-none" /><Bone className="h-11 w-32 shrink-0 !rounded-xl" /></div></div>

/* ------------------------------------------------------------------ one page skeleton per route */

const HomePage = () => (
  <Page>
    <div className="flex items-start justify-between gap-3"><Bone className="h-9 w-56" /><Bone className="h-9 w-40 !rounded-full" /></div>
    <KpiRowSkeleton />
    <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]"><PanelSkeleton height={200} /><PanelSkeleton height={150} /></div>
    <div className="grid gap-6 lg:grid-cols-[1fr_1.15fr]"><PanelSkeleton height={170} /><div className="card"><div className="px-5 pt-5"><Bone className="h-5 w-28" /></div><ListRowsSkeleton count={4} /></div></div>
  </Page>
)
const ChatsPage = () => (
  <div className="flex h-full min-h-0 flex-col" aria-busy="true" aria-label="Loading">
    <TabBar tabs={CHAT_TABS()} left={<h1 className="truncate font-display text-xl font-extrabold">Chats</h1>} />
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] lg:grid-cols-[360px_minmax(0,1fr)]">
      <div className="border-r border-line/70 bg-surface"><div className="space-y-3 border-b border-line/70 p-4"><Bone className="h-11 w-full !rounded-xl" /></div><ListRowsSkeleton /></div>
      <div className="hidden lg:block"><ThreadSkeleton /></div>
    </div>
  </div>
)
const PipelinePage = () => (
  <div className="flex h-full min-h-0 flex-col" aria-busy="true" aria-label="Loading">
    <TabBar tabs={CHAT_TABS()} left={<h1 className="truncate font-display text-xl font-extrabold">Pipeline</h1>} />
    <div className="flex min-h-0 flex-1 flex-col px-4 pb-3 pt-3 lg:px-8"><ColumnsSkeleton /></div>
  </div>
)
const CatalogPage = ({ title, sub, height }: { title: string; sub: string; height: number }) => (<Page wide><TitleBar title={title} sub={sub} /><Toolbar /><CardGridSkeleton height={height} /></Page>)
const InboxPage = () => (<Page><TitleBar title="For you" sub="Things only you can do. The assistant keeps everything else moving." /><StaticTabs labels={['Customers waiting', 'To confirm', 'Questions']} /><CardListSkeleton count={3} /></Page>)
const CustomersPage = () => (<Page><TitleBar title="Customers" sub="Everyone who has messaged your number. Mark family and friends as personal and the assistant will never reply to them." /><div className="flex flex-wrap items-center gap-3"><Bone className="h-11 w-full max-w-xs !rounded-xl" /></div><TableSkeleton rows={7} cols={4} /></Page>)
const AnalyticsPage = () => (<Page><SectionTabs tabs={INSIGHT_TABS()} /><TitleBar title="Analytics" sub="How your WhatsApp sales are going, compared with the period before." /><KpiRowSkeleton count={4} className="!grid-cols-2 lg:!grid-cols-4 xl:!grid-cols-4" /><div className="grid gap-6 lg:grid-cols-2"><PanelSkeleton /><PanelSkeleton /></div></Page>)
const ReportsPage = () => (<Page><SectionTabs tabs={INSIGHT_TABS()} /><TitleBar title="Reports" sub="A summary for any period, to print or share, and your data as spreadsheets." /><TableSkeleton rows={6} cols={5} /></Page>)
const BillingPage = () => (<Page><TitleBar title="Billing" sub="Your plan, usage and invoices." /><Bone className="h-36 w-full rounded-2xl" /><div className="grid gap-5 lg:grid-cols-3">{range(3).map((i) => <Bone key={i} className="h-72 rounded-2xl" />)}</div></Page>)
const SettingsPage = () => (<div className="mx-auto max-w-[900px] space-y-5 px-4 py-6 lg:px-8 lg:py-8" aria-busy="true" aria-label="Loading"><TitleBar title="Settings" sub="Everything about how your shop and assistant work." /><div className="-mx-4 overflow-x-auto px-4"><Segmented className="w-max" value="Shop" onChange={() => {}} options={['Shop', 'Assistant', 'Team', 'Numbers', 'Devices', 'Support'].map((l) => ({ value: l, label: l }))} /></div><div className="card card-pad"><FormSkeleton fields={4} /></div><div className="card card-pad"><FormSkeleton fields={3} /></div></div>)
const PlaygroundPage = () => (<Page><TitleBar title="Try the assistant" /><div className="flex justify-center"><Bone className="h-[620px] w-[304px] !rounded-[42px]" /></div></Page>)
const OperatorPage = () => (<Page><TitleBar title="Operator console" sub="Every business on the platform: health, volume, cost, and the plumbing underneath." /><div className="-mx-4 overflow-x-auto px-4"><Segmented className="w-max" value="Tenants" onChange={() => {}} options={['Tenants', 'Alerts', 'Support', 'Queues', 'Webhooks', 'Billing', 'AI turns', 'Costs'].map((l) => ({ value: l, label: l }))} /></div><TableSkeleton rows={6} cols={5} /></Page>)

/** One entry per route in `App.tsx`, keyed by the first path segment. `skeletons.test.ts` checks that no route is missing. */
export const PAGE_SKELETONS: Record<string, () => ReactNode> = {
  home: HomePage,
  chats: ChatsPage,
  pipeline: PipelinePage,
  products: () => <CatalogPage title="Products" sub="What the assistant can sell, and exactly how it may talk about price." height={200} />,
  offers: () => <CatalogPage title="Offers" sub="Deals the assistant may mention. It only uses offers you create here." height={196} />,
  inbox: InboxPage,
  customers: CustomersPage,
  analytics: AnalyticsPage,
  reports: ReportsPage,
  billing: BillingPage,
  settings: SettingsPage,
  try: PlaygroundPage,
  operator: OperatorPage,
}

/** Shown inside the app frame while a screen's code loads. Falls back to a plain page for any route not listed. */
export function RouteSkeleton() {
  const seg = useLocation().pathname.split('/').filter(Boolean)[0] ?? ''
  const Page_ = PAGE_SKELETONS[seg]
  return Page_ ? <>{Page_()}</> : <Page><CardListSkeleton /></Page>
}

/** The app frame as grey shapes: shown while we find out who is signed in, so the page does not flash blank. */
export function ShellSkeleton() {
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[264px_minmax(0,1fr)]" aria-busy="true" aria-label="Loading">
      <aside className="hidden border-r border-line/70 bg-surface p-3 lg:flex lg:flex-col lg:gap-3">
        <div className="flex items-center gap-2.5 px-2 py-2"><Bone className="h-9 w-9 !rounded-xl" /><Bone className="h-5 w-28" /></div>
        <Bone className="h-14 w-full !rounded-xl" />
        <div className="space-y-1.5">{range(9).map((i) => <Bone key={i} className="h-10 w-full !rounded-xl" />)}</div>
      </aside>
      <div className="flex min-w-0 flex-col pb-[76px] lg:pb-0">
        <div className="flex items-center gap-3 border-b border-line/60 px-4 py-3 lg:hidden"><Bone className="h-8 w-8 !rounded-lg" /><Bone className="h-5 w-40" /><Bone className="ml-auto h-9 w-9 !rounded-full" /></div>
        <RouteSkeleton />
      </div>
      <nav className="fixed inset-x-0 bottom-0 grid grid-cols-5 gap-2 border-t border-line/70 bg-surface px-3 py-3 lg:hidden">{range(5).map((i) => <Bone key={i} className="mx-auto h-9 w-12 !rounded-lg" />)}</nav>
    </div>
  )
}

/** The sign-in form as grey shapes. */
export function LoginSkeleton() {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-[400px] flex-col justify-center gap-5 px-5" aria-busy="true" aria-label="Loading">
      <Bone className="h-9 w-36" /><Bone className="h-9 w-3/4" /><Bone className="h-4 w-full" /><Bone className="h-12 w-full !rounded-xl" /><Bone className="h-12 w-full !rounded-xl" />
    </div>
  )
}
