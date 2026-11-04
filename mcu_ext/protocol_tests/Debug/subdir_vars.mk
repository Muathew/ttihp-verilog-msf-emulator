################################################################################
# Automatically-generated file. Do not edit!
################################################################################

SHELL = cmd.exe

# Add inputs and outputs from these tool invocations to the build variables 
SYSCFG_SRCS += \
../p_test.syscfg 

C_SRCS += \
../can_test.c \
../i2c_test.c \
../main.c \
./ti_msp_dl_config.c \
C:/ti/mspm0_sdk_2_11_00_07/source/ti/devices/msp/m0p/startup_system_files/ticlang/startup_mspm0g351x_ticlang.c \
../protocol_testh.c \
../spi_test.c \
../uart_test.c 

GEN_CMDS += \
./device_linker.cmd 

GEN_FILES += \
./device_linker.cmd \
./device.opt \
./ti_msp_dl_config.c 

C_DEPS += \
./can_test.d \
./i2c_test.d \
./main.d \
./ti_msp_dl_config.d \
./startup_mspm0g351x_ticlang.d \
./protocol_testh.d \
./spi_test.d \
./uart_test.d 

GEN_OPTS += \
./device.opt 

OBJS += \
./can_test.o \
./i2c_test.o \
./main.o \
./ti_msp_dl_config.o \
./startup_mspm0g351x_ticlang.o \
./protocol_testh.o \
./spi_test.o \
./uart_test.o 

GEN_MISC_FILES += \
./device.cmd.genlibs \
./ti_msp_dl_config.h 

OBJS__QUOTED += \
"can_test.o" \
"i2c_test.o" \
"main.o" \
"ti_msp_dl_config.o" \
"startup_mspm0g351x_ticlang.o" \
"protocol_testh.o" \
"spi_test.o" \
"uart_test.o" 

GEN_MISC_FILES__QUOTED += \
"device.cmd.genlibs" \
"ti_msp_dl_config.h" 

C_DEPS__QUOTED += \
"can_test.d" \
"i2c_test.d" \
"main.d" \
"ti_msp_dl_config.d" \
"startup_mspm0g351x_ticlang.d" \
"protocol_testh.d" \
"spi_test.d" \
"uart_test.d" 

GEN_FILES__QUOTED += \
"device_linker.cmd" \
"device.opt" \
"ti_msp_dl_config.c" 

C_SRCS__QUOTED += \
"../can_test.c" \
"../i2c_test.c" \
"../main.c" \
"./ti_msp_dl_config.c" \
"C:/ti/mspm0_sdk_2_11_00_07/source/ti/devices/msp/m0p/startup_system_files/ticlang/startup_mspm0g351x_ticlang.c" \
"../protocol_testh.c" \
"../spi_test.c" \
"../uart_test.c" 

SYSCFG_SRCS__QUOTED += \
"../p_test.syscfg" 


