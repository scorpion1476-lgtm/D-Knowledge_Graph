module counter (
    input clk,
    input rst,
    output reg [7:0] q
);

    always @(posedge clk) begin
        if (rst) q <= 0;
        else q <= q + 1;
    end

    function integer double_it(input integer a);
        double_it = a * 2;
    endfunction

    task reset_all;
        q = 0;
    endtask

endmodule

module top (
    input clk
);
    counter c (.clk(clk));
endmodule
