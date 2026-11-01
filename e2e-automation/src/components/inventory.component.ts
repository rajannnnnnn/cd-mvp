import { Page, Locator } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';

/** Component for the products page that appears after a successful login. */
export class InventoryComponent {
  private readonly title: Locator;
  private readonly items: Locator;
  private readonly cartBadge: Locator;

  constructor(private readonly page: Page, private readonly common: CommonFunctions) {
    this.title = page.locator('[data-test="title"]');
    this.items = page.locator('[data-test="inventory-item"]');
    this.cartBadge = page.locator('[data-test="shopping-cart-badge"]');
  }

  async expectLoaded(): Promise<void> {
    await this.common.expectUrlContains('/inventory.html');
    await this.common.expectText(this.title, 'Products');
  }

  async itemCount(): Promise<number> {
    await this.items.first().waitFor({ state: 'visible' });
    return this.items.count();
  }

  async addItemToCart(productName: string): Promise<void> {
    const item = this.items.filter({ hasText: productName });
    await this.common.click(item.getByRole('button', { name: /add to cart/i }));
  }

  async expectCartCount(count: number): Promise<void> {
    await this.common.expectText(this.cartBadge, String(count));
  }
}
