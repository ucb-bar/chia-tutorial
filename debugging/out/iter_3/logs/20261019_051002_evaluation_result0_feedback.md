The simulation did not pass: failing: odd_large (simulator exited with rc=255 (a nonzero rc after the cycle budget means the program hung)).

Each test and eval runs as its own program. A case PASSes when memcpy_rocc returns 1 and `check_copy` finds no mismatch (a wrong destination byte, a byte written outside the destination, or a changed source byte).

## Results
```
zero_bytes: PASS ret=1 mismatches=0
one_byte: PASS ret=1 mismatches=0
seven_bytes: PASS ret=1 mismatches=0
one_word: PASS ret=1 mismatches=0
one_word_src_unalign: PASS ret=1 mismatches=0
one_word_dst_unalign: PASS ret=1 mismatches=0
nine_bytes: PASS ret=1 mismatches=0
same_misalign: PASS ret=1 mismatches=0
diff_misalign: PASS ret=1 mismatches=0
cache_line: PASS ret=1 mismatches=0
cache_line_plus_one: PASS ret=1 mismatches=0
copy_256B: PASS ret=1 mismatches=0 bytes=256 cycles=184
copy_1KiB: PASS ret=1 mismatches=0 bytes=1024 cycles=520
copy_4KiB: PASS ret=1 mismatches=0 bytes=4096 cycles=1966
copy_16KiB: PASS ret=1 mismatches=0 bytes=16384 cycles=5741
copy_4KiB_unaligned: PASS ret=1 mismatches=0 bytes=4093 cycles=1932
```

## `odd_large`: simulator stdout
```
[UART] UART0 is here (stdin/stdout).
[3000001000] %Fatal: TestDriver.v:168: Assertion failed in TOP.TestDriver
%Error: /home/ray/chipyard/sims/verilator/generated-src/chipyard.harness.TestHarness.FastRTLSimRocketConfig/gen-collateral/TestDriver.v:168: Verilog $stop
Aborting...

```

## `odd_large`: simulator stderr (spike-dasm)
```
== Loading device model file '/home/ray/accel_sim/77438d7d/dramsim_ini/DDR3_micron_64M_8B_x4_sg15.ini' == 
== Loading system model file '/home/ray/accel_sim/77438d7d/dramsim_ini/system.ini' == 
===== MemorySystem 0 =====
CH. 0 TOTAL_STORAGE : 4096MB | 1 Ranks | 16 Devices per rank
DRAMSim2 Clock Frequency =666666666Hz, CPU Clock Frequency=500000000Hz
*** FAILED ***                       (timeout) after              3000001 simulation cycles

```

## Disassembly of memcpy_rocc
```
0000000080000364 <memcpy_rocc>:
    80000364:	02059793          	slli	a5,a1,0x20
    80000368:	0207d793          	srli	a5,a5,0x20
    8000036c:	02061613          	slli	a2,a2,0x20
    80000370:	00050593          	mv	a1,a0
    80000374:	00c7e633          	or	a2,a5,a2
    80000378:	00c5f50b          	.insn	4, 0x00c5f50b
    8000037c:	0005051b          	sext.w	a0,a0
    80000380:	00008067          	ret

0000000080000384 <_iprintf_r>:
    80000384:	715d                	addi	sp,sp,-80
    80000386:	f032                	sd	a2,32(sp)
    80000388:	f436                	sd	a3,40(sp)
    8000038a:	f83a                	sd	a4,48(sp)
    8000038c:	fc3e                	sd	a5,56(sp)
    8000038e:	e0c2                	sd	a6,64(sp)
    80000390:	e4c6                	sd	a7,72(sp)
    80000392:	862e                	mv	a2,a1
    80000394:	690c                	ld	a1,16(a0)
    80000396:	1014                	addi	a3,sp,32
    80000398:	ec06                	sd	ra,24(sp)
    8000039a:	e436                	sd	a3,8(sp)
    8000039c:	0dc000ef          	jal	80000478 <_vfiprintf_r>
    800003a0:	60e2                	ld	ra,24(sp)
    800003a2:	6161                	addi	sp,sp,80
    800003a4:	8082                	ret

00000000800003a6 <iprintf>:
    800003a6:	711d                	addi	sp,sp,-96
    800003a8:	f832                	sd	a2,48(sp)
    800003aa:	fc36                	sd	a3,56(sp)
    800003ac:	e4be                	sd	a5,72(sp)
    800003ae:	f42e                	sd	a1,40(sp)
    800003b0:	e0ba                	sd	a4,64(sp)
    800003b2:	e8c2                	sd	a6,80(sp)
    800003b4:	ecc6                	sd	a7,88(sp)
    800003b6:	00002797          	auipc	a5,0x2
    800003ba:	b827b783          	ld	a5,-1150(a5) # 80001f38 <_impure_ptr>
    800003be:	6b8c                	ld	a1,16(a5)
    800003c0:	1034                	addi	a3,sp,40
    800003c2:	862a                	mv	a2,a0
    800003c4:	853e                	mv	a0,a5
    800003c6:	ec06                	sd	ra,24(sp)
    800003c8:	e436                	sd	a3,8(sp)
    800003ca:	0ae000ef          	jal	80000478 <_vfiprintf_r>
    800003ce:	60e2                	ld	ra,24(sp)
    800003d0:	6125                	addi	sp,sp,96
    800003d2:	8082                	ret

00000000800003d4 <memset>:
    800003d4:	832a                	mv	t1,a0
    800003d6:	c611                	beqz	a2,800003e2 <memset+0xe>
    800003d8:	00b30023          	sb	a1,0(t1)
    800003dc:	167d                	addi	a2,a2,-1
    800003de:	0305                	addi	t1,t1,1
    800003e0:	fe65                	bnez	a2,800003d8 <memset+0x4>
    800003e2:	8082                	ret

00000000800003e4 <__sfputc_r>:
    800003e4:	465c                	lw	a5,12(a2)
    800003e6:	fff7871b          	addiw	a4,a5,-1
    800003ea:	c658                	sw	a4,12(a2)
    800003ec:	00075a63          	bgez	a4,80000400 <__sfputc_r+0x1c>
    800003f0:	561c                	lw	a5,40(a2)
    800003f2:	00f74563          	blt	a4,a5,800003fc <__sfputc_r+0x18>
    800003f6:	47a9                	li	a5,10
    800003f8:	00f59463          	bne	a1,a5,80000400 <__sfputc_r+0x1c>
    800003fc:	4b90006f          	j	800010b4 <__swbuf_r>
    80000400:	621c                	ld	a5,0(a2)
    80000402:	852e                	mv	a0,a1
    80000404:	00178713          	addi	a4,a5,1
    80000408:	e218                	sd	a4,0(a2)
    8000040a:	00b78023          	sb	a1,0(a5)
    8000040e:	8082                	ret

0000000080000410 <__sfputs_r>:
    80000410:	7179                	addi	sp,sp,-48
    80000412:	f022                	sd	s0,32(sp)
    80000414:	ec26                	sd	s1,24(sp)
    80000416:	e84a                	sd	s2,16(sp)
    80000418:	e44e                	sd	s3,8(sp)
    8000041a:	e052                	sd	s4,0(sp)
    8000041c:	f406                	sd	ra,40(sp)
    8000041e:	892a                	mv	s2,a0
    80000420:	89ae                	mv	s3,a1
    80000422:	8432                	mv	s0,a2
    80000424:	00d604b3          	add	s1,a2,a3
    80000428:	5a7d                	li	s4,-1
    8000042a:	00941463          	bne	s0,s1,80000432 <__sfputs_r+0x22>
    8000042e:	4501                	li	a0,0
    80000430:	a811                	j	80000444 <__sfputs_r+0x34>
    80000432:	00044583          	lbu	a1,0(s0)
    80000436:	864e                	mv	a2,s3
    80000438:	854a                	mv	a0,s2
    8000043a:	fabff0ef          	jal	800003e4 <__sfputc_r>
    8000043e:	0405                	addi	s0,s0,1
    80000440:	ff4515e3          	bne	a0,s4,8000042a <__sfputs_r+0x1a>
    80000444:	70a2                	ld	ra,40(sp)
    80000446:	7402                	ld	s0,32(sp)
    80000448:	64e2                	ld	s1,24(sp)
    8000044a:	6942                	ld	s2,16(sp)
    8000044c:	69a2                	ld	s3,8(sp)
    8000044e:	6a02                	ld	s4,0(sp)
    80000450:	6145                	addi	sp,sp,48
    80000452:	8082                	ret

0000000080000454 <__sprint_r>:
    80000454:	6a1c                	ld	a5,16(a2)
    80000456:	1141                	addi	sp,sp,-16
    80000458:	e022                	sd	s0,0(sp)
    8000045a:	e406                	sd	ra,8(sp)
    8000045c:	8432                	mv	s0,a2
    8000045e:	cb99                	beqz	a5,80000474 <__sprint_r+0x20>
    80000460:	051000ef          	jal	80000cb0 <__sfvwrite_r>
    80000464:	00043823          	sd	zero,16(s0)
    80000468:	60a2                	ld	ra,8(sp)
    8000046a:	00042423          	sw	zero,8(s0)
    8000046e:	6402                	ld	s0,0(sp)
    80000470:	0141                	addi	sp,sp,16
```
