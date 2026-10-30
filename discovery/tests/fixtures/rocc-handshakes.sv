// Directed regression for same-edge RoCC commands, responses, and memory traffic.
module RoCCHandshakes;
  bit clock=0;
  always #5 clock=~clock;
  bit reset=1, busy=0, cmd_valid=0, cmd_ready=1, xd=1, xs1=1, xs2=1;
  logic [6:0] funct=0;
  logic [63:0] rs1=64'h80000000, rs2=64'h80001000, status=0;
  bit resp_valid=0, resp_ready=1, a_valid=0, a_ready=1, d_valid=0, d_ready=1;
  logic [2:0] a_opcode=4, d_opcode=1;
  logic [3:0] a_size=3, d_size=3;
  bit a_source=0, d_source=0, d_denied=0, d_corrupt=0;
  string scenario;
  DMAPassiveMonitor dut(.*);
  task automatic tick; @(negedge clock); endtask
  initial begin
    if (!$value$plusargs("case=%s", scenario)) $fatal(1, "Missing case");
    tick(); reset=0;
    case (scenario)
      "immediate-config": begin
        cmd_valid=1; resp_valid=1; tick();
        assert(dut.configs==1 && !dut.pending);
      end
      "immediate-copy": begin
        funct=1; rs1=0; cmd_valid=1; resp_valid=1; tick();
        assert(dut.copies==1 && dut.completed==1 && !dut.pending);
        assert(dut.copy_cycle==dut.valid_cycle);
      end
      "backpressure": begin
        funct=1; rs1=0; cmd_valid=1; resp_valid=1; resp_ready=0; tick();
        cmd_valid=0; repeat(2) tick();
        assert(dut.pending && dut.completed==0 && dut.resp_stall==3);
        assert(dut.copy_cycle==dut.valid_cycle);
        resp_ready=1; tick();
        assert(!dut.pending && dut.completed==1);
      end
      "turnover": begin
        // The response retires COPY; CONFIG's addresses must be recorded afterward.
        funct=1; rs1=0; cmd_valid=1; tick();
        funct=0; rs1=64'h80002000; resp_valid=1; tick();
        assert(dut.completed==1 && dut.configs==1 && dut.pending);
        assert(dut.source_address==64'h80002000 && !dut.copy_pending);
        cmd_valid=0; tick();
        assert(!dut.pending && dut.completed==1);
      end
      "turnover-to-copy": begin
        cmd_valid=1; tick();
        funct=1; rs1=0; resp_valid=1; tick();
        assert(dut.copy_pending && dut.pending && dut.completed==0);
        cmd_valid=0; tick();
        assert(!dut.pending && dut.completed==1);
      end
      "memory-on-command": begin
        funct=1; rs1=8; cmd_valid=1; a_valid=1; tick();
        cmd_valid=0; a_valid=0; d_valid=1; tick();
        d_valid=0; a_valid=1; a_opcode=0; tick();
        a_valid=0; d_valid=1; d_opcode=0; resp_valid=1; tick();
        assert(dut.completed==1 && dut.read_bytes==8 && dut.write_bytes==8);
        assert(dut.outstanding==0 && !dut.pending);
      end
      "unsolicited": begin
        resp_valid=1; tick();
        $fatal(1, "Expected protocol violation");
      end
      "unaccepted": begin
        cmd_valid=1; cmd_ready=0; resp_valid=1; tick();
        $fatal(1, "Expected protocol violation");
      end
      "no-destination": begin
        cmd_valid=1; xd=0; resp_valid=1; tick();
        $fatal(1, "Expected protocol violation");
      end
      "overlap": begin
        cmd_valid=1; tick(); tick();
        $fatal(1, "Expected protocol violation");
      end
      "backpressured-overlap": begin
        cmd_valid=1; tick(); resp_valid=1; resp_ready=0; tick();
        $fatal(1, "Expected protocol violation");
      end
      "missing-traffic": begin
        funct=1; rs1=8; cmd_valid=1; resp_valid=1; tick();
        $fatal(1, "Expected protocol violation");
      end
      "unacknowledged": begin
        funct=1; rs1=8; cmd_valid=1; a_valid=1; resp_valid=1; tick();
        $fatal(1, "Expected protocol violation");
      end
      default: $fatal(1, "Unknown case");
    endcase
    cmd_valid=0; resp_valid=0; d_valid=0; tick();
    assert(dut.protocol_errors==0 && !dut.pending && dut.outstanding==0);
    $display("ROCC_HANDSHAKES PASS");
    $finish;
  end
endmodule
