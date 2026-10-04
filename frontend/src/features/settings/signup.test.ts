import { describe, expect, it } from 'vitest'
import { parseSignupMessage } from './signup'

const FB = 'https://www.facebook.com'
const finish = (event: string, data: object = { waba_id: 'w1', phone_number_id: 'p1' }) => ({ type: 'WA_EMBEDDED_SIGNUP', event, data })

describe('Embedded Signup popup messages', () => {
  it('reads the account and number ids when the flow finishes', () => {
    expect(parseSignupMessage(FB, JSON.stringify(finish('FINISH')))).toEqual({ kind: 'finish', value: { waba_id: 'w1', phone_number_id: 'p1', coexistence: false } })
  })
  it('marks coexistence when the number stays on the Business app', () => {
    expect(parseSignupMessage(FB, finish('FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING'))).toMatchObject({ kind: 'finish', value: { coexistence: true } })
  })
  it('ignores messages from any other origin (a page cannot fake the sign-up)', () => {
    expect(parseSignupMessage('https://evil.example', finish('FINISH'))).toBeNull()
    expect(parseSignupMessage('https://www.facebook.com.evil.example', finish('FINISH'))).toBeNull()
  })
  it('ignores unrelated messages and malformed payloads', () => {
    expect(parseSignupMessage(FB, 'not json')).toBeNull()
    expect(parseSignupMessage(FB, { type: 'something-else' })).toBeNull()
    expect(parseSignupMessage(FB, null)).toBeNull()
  })
  it('reports cancellation, errors and missing ids', () => {
    expect(parseSignupMessage(FB, finish('CANCEL'))).toEqual({ kind: 'cancel' })
    expect(parseSignupMessage(FB, finish('FINISH', {}))).toMatchObject({ kind: 'error' })
    expect(parseSignupMessage(FB, { type: 'WA_EMBEDDED_SIGNUP', event: 'ERROR', data: { error_message: 'boom' } })).toEqual({ kind: 'error', message: 'boom' })
  })
})
