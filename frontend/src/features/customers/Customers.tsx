import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, MessageCircle, Search, UserRound, Users } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Avatar, Badge, EmptyState, ErrorNote, Segmented, Skeleton, Spinner, useToast } from '@/ui'
import { ago, phone } from '@/lib/format'
import { tr } from '@/i18n/tr'

type Filter = 'all' | 'personal' | 'opted_out'

export default function Customers() {
  const { t } = useT()
  const toast = useToast(); const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState(''); const [deb, setDeb] = useState('')
  useEffect(() => { const id = setTimeout(() => setDeb(search), 250); return () => clearTimeout(id) }, [search])
  const q = useInfiniteQuery({
    queryKey: ['customers', filter, deb], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => ok(api.GET('/api/v1/customers', { params: { query: { limit: 50, cursor: pageParam, ...(deb ? { search: deb } : {}), ...(filter === 'personal' ? { personal: true } : {}), ...(filter === 'opted_out' ? { opted_out: true } : {}) } as any } })),
    getNextPageParam: (l) => l.next_cursor ?? undefined,
  })
  const patch = useMutation({
    mutationFn: (v: { id: string; body: Schemas['CustomerPatch'] }) => ok(api.PATCH('/api/v1/customers/{customer_id}', { params: { path: { customer_id: v.id } }, body: v.body })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['customers'] }); qc.invalidateQueries({ queryKey: ['conversations'] }); toast(tr('Updated')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div className="mx-auto max-w-[980px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">{t('nav.customers', 'Customers')}</h1><p className="text-[15px] text-muted">{tr('Everyone who has messaged your number. Mark family and friends as personal and the assistant will never reply to them.')}</p></div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1 sm:max-w-xs"><Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input className="input pl-10" placeholder={tr('Search name or number')} value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        <Segmented value={filter} onChange={setFilter} options={[{ value: 'all', label: tr('All') }, { value: 'personal', label: tr('Personal') }, { value: 'opted_out', label: tr('Opted out') }]} />
      </div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-64 rounded-2xl" />}
      {!q.isLoading && !q.error && items.length === 0 && <div className="card"><EmptyState icon={<Users className="h-6 w-6" />} title={tr('No customers found')} body={filter === 'all' ? tr('Customers appear here after their first message.') : tr('Nobody matches this filter.')} /></div>}
      {items.length > 0 && (
        <div className="card divide-y divide-line/60 overflow-hidden">
          {items.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3.5 sm:px-5">
              <Avatar name={c.name ?? c.phone} size={40} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><span className="truncate font-bold">{c.name ?? phone(c.phone)}</span>{c.is_personal && <Badge tone="blue"><UserRound className="h-3 w-3" /> {tr('Personal')}</Badge>}{c.opted_out && <Badge tone="red"><Ban className="h-3 w-3" /> {tr('Opted out')}</Badge>}{(c.won_deals ?? 0) > 0 && <Badge tone="green">{c.won_deals} {tr('order')}{c.won_deals === 1 ? '' : 's'}</Badge>}</div>
                <div className="text-[13px] text-muted">{c.name ? `${phone(c.phone)} · ` : ''}{tr('last seen {when}', { when: ago(c.last_seen_at) })}</div>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-outline btn-sm" disabled={patch.isPending} onClick={() => patch.mutate({ id: c.id, body: { is_personal: !c.is_personal } })}>{patch.isPending ? <Spinner /> : <UserRound className="h-3.5 w-3.5" />}{c.is_personal ? tr('Not personal') : tr('Personal')}</button>
                {c.opted_out && <button className="btn btn-outline btn-sm" onClick={() => patch.mutate({ id: c.id, body: { opted_out: false } })}>{tr('Allow messages')}</button>}
              </div>
            </div>
          ))}
          {q.hasNextPage && <div className="p-3 text-center"><button className="btn btn-ghost btn-sm" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? <Spinner /> : null} {tr('Load more')}</button></div>}
        </div>
      )}
      <p className="flex items-center gap-2 text-[13px] text-muted"><MessageCircle className="h-4 w-4" /> {tr('Open a customer’s chat from')} <Link className="font-semibold text-brand-ink underline-offset-2 hover:underline" to="/chats">{tr('Chats')}</Link>.</p>
    </div>
  )
}
