pragma solidity ^0.8.0;

import "./Token.sol";

contract Vault {
    function deposit(uint amount) public returns (uint) {
        return MathLib.add(amount, 1);
    }

    function withdraw(uint amount) public returns (uint) {
        return deposit(amount);
    }
}
