import { setWorldConstructor, World, IWorldOptions } from '@cucumber/cucumber';
import { BrowserContext, Page } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';
import { LoginComponent } from '../components/login.component';
import { InventoryComponent } from '../components/inventory.component';

/**
 * World = the per-scenario "backpack". Hooks create the page, steps read it.
 * Components are built lazily so each scenario gets fresh instances.
 */
export class CustomWorld extends World {
  context!: BrowserContext;
  page!: Page;
  scenarioName = '';

  private _common?: CommonFunctions;
  private _login?: LoginComponent;
  private _inventory?: InventoryComponent;

  constructor(options: IWorldOptions) {
    super(options);
  }

  get common(): CommonFunctions {
    return (this._common ??= new CommonFunctions(this.page));
  }
  get loginComponent(): LoginComponent {
    return (this._login ??= new LoginComponent(this.page, this.common));
  }
  get inventoryComponent(): InventoryComponent {
    return (this._inventory ??= new InventoryComponent(this.page, this.common));
  }
}

setWorldConstructor(CustomWorld);
