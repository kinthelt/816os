; macros.inc - register width switching
;
; Each macro changes the CPU's register width and tells the assembler about it,
; so the two can't get out of step.

.macro A8
        sep #$20
        .a8
.endmacro

.macro A16
        rep #$20
        .a16
.endmacro

.macro I8
        sep #$10
        .i8
.endmacro

.macro I16
        rep #$10
        .i16
.endmacro

.macro AXY8
        sep #$30
        .a8
        .i8
.endmacro

.macro AXY16
        rep #$30
        .a16
        .i16
.endmacro
