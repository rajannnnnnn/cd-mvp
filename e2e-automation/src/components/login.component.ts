import { Page, Locator } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';

/** Component (page-object) for the login screen: locators + actions, no assertions logic leaks to steps. */
export class LoginComponent {
  private readonly usernameInput: Locator;
  private readonly passwordInput: Locator;
  private readonly loginButton: Locator;
  private readonly errorMessage: Locator;

  constructor(private readonly page: Page, private readonly common: CommonFunctions) {
    this.usernameInput = page.locator('[data-test="username"]');
    this.passwordInput = page.locator('[data-test="password"]');
    this.loginButton = page.locator('[data-test="login-button"]');
    this.errorMessage = page.locator('[data-test="error"]');
  }

  async open(): Promise<void> {
    await this.common.navigateTo('/');
    await this.common.expectVisible(this.loginButton, 'Login page did not load');
  }

  async login(username: string, password: string): Promise<void> {
    await this.common.type(this.usernameInput, username);
    await this.common.type(this.passwordInput, password);
    await this.common.click(this.loginButton);
  }

  async expectError(text: string): Promise<void> {
    await this.common.expectVisible(this.errorMessage);
    await this.common.expectText(this.errorMessage, text);
  }
}
