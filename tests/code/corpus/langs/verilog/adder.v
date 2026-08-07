module adder (
    input [7:0] a,
    input [7:0] b,
    output [8:0] sum
);
    assign sum = a + b;

    function integer widen(input integer x);
        widen = x;
    endfunction
endmodule
