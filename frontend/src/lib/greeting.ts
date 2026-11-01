/** The line at the top of the dashboard: it follows the time of day and the day of the week (the visitor's own clock). */
export interface Greeting { hello: string; emoji: string; day: string }

const DAY_LINE = [
  'Have a relaxed Sunday.',            // 0
  'A fresh week begins.',              // 1 Monday
  'Hope Tuesday is going well.',       // 2
  'Halfway through the week.',         // 3
  'Almost at the weekend.',            // 4
  'Happy Friday.',                     // 5
  'Happy Saturday.',                   // 6
] as const

export function greeting(now: Date = new Date()): Greeting {
  const h = now.getHours()
  const [hello, emoji] = h >= 5 && h < 12 ? ['Good morning', '☀️'] : h >= 12 && h < 17 ? ['Good afternoon', '🌤️'] : h >= 17 && h < 21 ? ['Good evening', '🌆'] : ['Hello', '🌙']
  return { hello, emoji, day: DAY_LINE[now.getDay()] }
}
