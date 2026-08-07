pragma solidity ^0.8.0;

import "./Base.sol";

interface IToken {
    function total() external view returns (uint);
}

library MathLib {
    function add(uint a, uint b) internal pure returns (uint) {
        return a + b;
    }
}

contract Base {
    uint internal supply;

    function helper() public view returns (uint) {
        return supply;
    }
}

contract Token is Base, IToken {
    struct Holder {
        address account;
    }

    enum State {
        Active,
        Frozen
    }

    constructor() {
        supply = 0;
    }

    modifier onlyActive() {
        _;
    }

    function total() external view returns (uint) {
        return helper();
    }
}
