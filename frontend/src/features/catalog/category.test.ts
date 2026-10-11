import { describe, expect, it } from 'vitest'
import { categoriesOf, didYouMean, norm, similar } from './category'

describe('category helpers', () => {
  it('treats plural, case and spacing as the same category', () => {
    expect(similar('Sarees', 'saree')).toBe(true)
    expect(similar('Dresses', 'Dress')).toBe(true)
    expect(similar('Home  Decor', 'home-decor')).toBe(true)
    expect(similar('Accessories', 'Accessory')).toBe(true)
    expect(norm('Kurtis ')).toBe('kurti')
  })
  it('catches a one-letter slip in a longer word but not different short words', () => {
    expect(similar('Jewellery', 'Jewelery')).toBe(true)
    expect(similar('Shirts', 'Shorts')).toBe(false)
    expect(similar('Sarees', 'Suits')).toBe(false)
    expect(similar('Bags', 'Bag')).toBe(true)
  })
  it('lists distinct categories, most used first, one spelling per case-variant', () => {
    expect(categoriesOf([{ category: 'Sarees' }, { category: 'sarees' }, { category: 'Sarees' }, { category: 'Suits' }, { category: null }, { category: ' ' }, { category: 'Suits' }, { category: 'Dupattas' }]))
      .toEqual(['Sarees', 'Suits', 'Dupattas'])
  })
  it('suggests the existing spelling only when the text is not already exact', () => {
    const have = ['Sarees', 'Suits']
    expect(didYouMean('Sarees', have)).toBeNull()
    expect(didYouMean('saree', have)).toBe('Sarees')
    expect(didYouMean('SAREES', have)).toBe('Sarees')
    expect(didYouMean('Kurtis', have)).toBeNull()
    expect(didYouMean('', have)).toBeNull()
  })
})
