@login
Feature: Login and shopping basics
  As a shopper
  I want to log in and add products to my cart
  So that I can buy what I need

  Background:
    Given the user is on the login page

  @smoke
  Scenario: Successful login with valid credentials
    When the user logs in with the valid credentials
    Then the products page is displayed
    And 6 products are listed

  @negative
  Scenario: Locked out user cannot log in
    When the user logs in as the locked out user
    Then the error message "Sorry, this user has been locked out" is shown

  @negative
  Scenario Outline: Invalid credentials are rejected
    When the user logs in with username "<username>" and password "<password>"
    Then the error message "<error>" is shown

    Examples:
      | username      | password     | error                                       |
      | standard_user | wrong_pass   | Username and password do not match          |
      | not_a_user    | secret_sauce | Username and password do not match          |
      |               | secret_sauce | Username is required                         |
      | standard_user |              | Password is required                         |

  @smoke @cart
  Scenario: Add a product to the cart
    Given the user is logged in
    When the user adds "Sauce Labs Backpack" to the cart
    Then the cart badge shows 1
