import { Given, When, Then } from '@cucumber/cucumber';
import { expect } from '@playwright/test';
import { CustomWorld } from '../support/world';
import { ENV } from '../config/env';

Given('the user is on the login page', async function (this: CustomWorld) {
  await this.loginComponent.open();
});

Given('the user is logged in', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.validUsername, ENV.users.validPassword);
  await this.inventoryComponent.expectLoaded();
});

When('the user logs in with the valid credentials', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.validUsername, ENV.users.validPassword);
});

When('the user logs in as the locked out user', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.lockedUsername, ENV.users.validPassword);
});

When(
  'the user logs in with username {string} and password {string}',
  async function (this: CustomWorld, username: string, password: string) {
    await this.loginComponent.login(username, password);
  },
);

When('the user adds {string} to the cart', async function (this: CustomWorld, product: string) {
  await this.inventoryComponent.addItemToCart(product);
});

Then('the products page is displayed', async function (this: CustomWorld) {
  await this.inventoryComponent.expectLoaded();
});

Then('{int} products are listed', async function (this: CustomWorld, expected: number) {
  expect(await this.inventoryComponent.itemCount()).toBe(expected);
});

Then('the error message {string} is shown', async function (this: CustomWorld, text: string) {
  await this.loginComponent.expectError(text);
});

Then('the cart badge shows {int}', async function (this: CustomWorld, count: number) {
  await this.inventoryComponent.expectCartCount(count);
});
