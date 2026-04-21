R"(#ifdef cl_khr_fp16
)"
R"(#pragma OPENCL EXTENSION cl_khr_fp16 : enable
)"
R"(#elif defined(cl_amd_fp16)
)"
R"(#pragma OPENCL EXTENSION cl_amd_fp16 : enable
)"
R"(#else
)"
R"(#error "Half precision floating point not supportedby OpenCL implementation on your device."
)"
R"(#endif
)"
R"(
)"
R"(#ifdef cl_khr_subgroups
)"
R"(#pragma OPENCL EXTENSION cl_khr_subgroups : enable
)"
R"(#elif defined(cl_intel_subgroups)
)"
R"(#pragma OPENCL EXTENSION cl_intel_subgroups : enable
)"
R"(#else
)"
R"(#error "Subgroup not supported on your device."
)"
R"(#endif
)"
R"(
)"
R"(#ifdef cl_intel_required_subgroup_size
)"
R"(// Always use subgroup size of 32 on Intel.
)"
R"(#pragma OPENCL EXTENSION cl_intel_required_subgroup_size : enable
)"
R"(#define INTEL_GPU 1
)"
R"(#define REQD_SUBGROUP_SIZE_16 __attribute__((intel_reqd_sub_group_size(16)))
)"
R"(#define REQD_SUBGROUP_SIZE_32 __attribute__((intel_reqd_sub_group_size(32)))
)"
R"(#elif defined(cl_qcom_reqd_sub_group_size)
)"
R"(// Always use subgroups size of 64 on Adreno.
)"
R"(#pragma OPENCL EXTENSION cl_qcom_reqd_sub_group_size : enable
)"
R"(#define ADRENO_GPU 1
)"
R"(#define REQD_SUBGROUP_SIZE_64  __attribute__((qcom_reqd_sub_group_size("half")))
)"
R"(#define REQD_SUBGROUP_SIZE_128 __attribute__((qcom_reqd_sub_group_size("full")))
)"
R"(#else
)"
R"(// TODO: do not know how to choose subgroup size on other GPUs.
)"
R"(#error "Selecting subgroup size is not supported on your device."
)"
R"(#endif
)"
R"(
)"
R"(#define QK4_0                   32
)"
R"(#define QR4_0                   2
)"
R"(#define QK4_1                   32
)"
R"(#define QR4_1                   2
)"
R"(#define QK5_0                   32
)"
R"(#define QR5_0                   2
)"
R"(#define QK5_1                   32
)"
R"(#define QR5_1                   2
)"
R"(#define QK8_0                   32
)"
R"(#define QR8_0                   1
)"
R"(#define QK_K                    256
)"
R"(#define K_QUANTS_PER_ITERATION  2
)"
R"(
)"
R"(typedef char int8_t;
)"
R"(typedef uchar uint8_t;
)"
R"(typedef short int16_t;
)"
R"(typedef ushort uint16_t;
)"
R"(typedef int int32_t;
)"
R"(typedef uint uint32_t;
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q4_0
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q4_0
)"
R"({
)"
R"(    half d;
)"
R"(    uint8_t qs[QK4_0 / 2];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q4_1
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q4_1
)"
R"({
)"
R"(    half d;
)"
R"(    half m;
)"
R"(    uint8_t qs[QK4_1 / 2];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q5_0
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q5_0
)"
R"({
)"
R"(    half d;
)"
R"(    uint32_t qh;
)"
R"(    uint8_t qs[QK5_0 / 2];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q5_1
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q5_1
)"
R"({
)"
R"(    half d;
)"
R"(    half m;
)"
R"(    uint32_t qh;
)"
R"(    uint8_t qs[QK5_1 / 2];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q8_0
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q8_0
)"
R"({
)"
R"(    half d;
)"
R"(    int8_t qs[QK8_0];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q2_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q2_K
)"
R"({
)"
R"(    uint8_t scales[16];
)"
R"(    uint8_t qs[64];
)"
R"(    half d;
)"
R"(    half dmin;
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q3_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q3_K
)"
R"({
)"
R"(    uint8_t hmask[32];
)"
R"(    uint8_t qs[64];
)"
R"(    uint8_t scales[12];
)"
R"(    half d;
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q4_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q4_K
)"
R"({
)"
R"(    half d;
)"
R"(    half dmin;
)"
R"(    uint8_t scales[12];
)"
R"(    uint8_t qs[128];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q5_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q5_K
)"
R"({
)"
R"(    half d;
)"
R"(    half dmin;
)"
R"(    uint8_t scales[12];
)"
R"(    uint8_t qh[32];
)"
R"(    uint8_t qs[128];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q6_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q6_K
)"
R"({
)"
R"(    uint8_t ql[128];
)"
R"(    uint8_t qh[64];
)"
R"(    int8_t scales[16];
)"
R"(    half d;
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// dequantize_q4_0_f32, dequantize_q4_0_f16
)"
R"(//------------------------------------------------------------------------------
)"
R"(void dequantize_q4_0_f32(global struct block_q4_0 * xb, short il, float16 * reg) {
)"
R"(    global ushort * qs = ((global ushort *)xb + 1);
)"
R"(    float d1 = il ? (xb->d / 16.h) : xb->d;
)"
R"(    float d2 = d1 / 256.f;
)"
R"(    float md = -8.h * xb->d;
)"
R"(    ushort mask0 = il ? 0x00F0 : 0x000F;
)"
R"(    ushort mask1 = mask0 << 8;
)"
R"(
)"
R"(    reg->s0 = d1 * (qs[0] & mask0) + md;
)"
R"(    reg->s1 = d2 * (qs[0] & mask1) + md;
)"
R"(
)"
R"(    reg->s2 = d1 * (qs[1] & mask0) + md;
)"
R"(    reg->s3 = d2 * (qs[1] & mask1) + md;
)"
R"(
)"
R"(    reg->s4 = d1 * (qs[2] & mask0) + md;
)"
R"(    reg->s5 = d2 * (qs[2] & mask1) + md;
)"
R"(
)"
R"(    reg->s6 = d1 * (qs[3] & mask0) + md;
)"
R"(    reg->s7 = d2 * (qs[3] & mask1) + md;
)"
R"(
)"
R"(    reg->s8 = d1 * (qs[4] & mask0) + md;
)"
R"(    reg->s9 = d2 * (qs[4] & mask1) + md;
)"
R"(
)"
R"(    reg->sa = d1 * (qs[5] & mask0) + md;
)"
R"(    reg->sb = d2 * (qs[5] & mask1) + md;
)"
R"(
)"
R"(    reg->sc = d1 * (qs[6] & mask0) + md;
)"
R"(    reg->sd = d2 * (qs[6] & mask1) + md;
)"
R"(
)"
R"(    reg->se = d1 * (qs[7] & mask0) + md;
)"
R"(    reg->sf = d2 * (qs[7] & mask1) + md;
)"
R"(}
)"
R"(
)"
R"(void dequantize_q4_0_f16(global struct block_q4_0 * xb, short il, half16 * reg) {
)"
R"(    global ushort * qs = ((global ushort *)xb + 1);
)"
R"(    half d1 = il ? (xb->d / 16.h) : xb->d;
)"
R"(    half d2 = d1 / 256.h;
)"
R"(    half md = -8.h * xb->d;
)"
R"(    ushort mask0 = il ? 0x00F0 : 0x000F;
)"
R"(    ushort mask1 = mask0 << 8;
)"
R"(
)"
R"(    reg->s0 = d1 * (qs[0] & mask0) + md;
)"
R"(    reg->s1 = d2 * (qs[0] & mask1) + md;
)"
R"(
)"
R"(    reg->s2 = d1 * (qs[1] & mask0) + md;
)"
R"(    reg->s3 = d2 * (qs[1] & mask1) + md;
)"
R"(
)"
R"(    reg->s4 = d1 * (qs[2] & mask0) + md;
)"
R"(    reg->s5 = d2 * (qs[2] & mask1) + md;
)"
R"(
)"
R"(    reg->s6 = d1 * (qs[3] & mask0) + md;
)"
R"(    reg->s7 = d2 * (qs[3] & mask1) + md;
)"
R"(
)"
R"(    reg->s8 = d1 * (qs[4] & mask0) + md;
)"
R"(    reg->s9 = d2 * (qs[4] & mask1) + md;
)"
R"(
)"
R"(    reg->sa = d1 * (qs[5] & mask0) + md;
)"
R"(    reg->sb = d2 * (qs[5] & mask1) + md;
)"
R"(
)"
R"(    reg->sc = d1 * (qs[6] & mask0) + md;
)"
R"(    reg->sd = d2 * (qs[6] & mask1) + md;
)"
R"(
)"
R"(    reg->se = d1 * (qs[7] & mask0) + md;
)"
R"(    reg->sf = d2 * (qs[7] & mask1) + md;
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// add
)"
R"(//------------------------------------------------------------------------------
)"
R"(
)"
R"(// general-purpose kernel for addition of two tensors
)"
R"(// pros: works for non-contiguous tensors, supports broadcast across dims 1, 2 and 3
)"
R"(// cons: not very efficient
)"
R"(kernel void kernel_add(
)"
R"(        global char * src0,
)"
R"(        ulong  offset0,
)"
R"(        global char * src1,
)"
R"(        ulong  offset1,
)"
R"(        global char * dst,
)"
R"(        ulong  offsetd,
)"
R"(        int   ne00,
)"
R"(        int   ne01,
)"
R"(        int   ne02,
)"
R"(        int   ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int   ne10,
)"
R"(        int   ne11,
)"
R"(        int   ne12,
)"
R"(        int   ne13,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int   ne0,
)"
R"(        int   ne1,
)"
R"(        int   ne2,
)"
R"(        int   ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(    src0 = src0 + offset0;
)"
R"(    src1 = src1 + offset1;
)"
R"(    dst = dst + offsetd;
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int i13 = i03 % ne13;
)"
R"(    int i12 = i02 % ne12;
)"
R"(    int i11 = i01 % ne11;
)"
R"(
)"
R"(    global char * src0_ptr = src0 + i03*nb03 + i02*nb02 + i01*nb01;
)"
R"(    global char * src1_ptr = src1 + i13*nb13 + i12*nb12 + i11*nb11;
)"
R"(    global char * dst_ptr  = dst  + i03*nb3  + i02*nb2  + i01*nb1;
)"
R"(
)"
R"(    for (int i0 = get_local_id(0); i0 < ne0; i0 += get_local_size(0)) {
)"
R"(        const int i10 = i0 % ne10;
)"
R"(        *((global float *)(dst_ptr + i0*nb0)) = *((global float *)(src0_ptr + i0*nb00)) + *((global float *)(src1_ptr + i10*nb10));
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(// assumption: src1 is a row
)"
R"(// broadcast src1 into src0
)"
R"(kernel void kernel_add_row(
)"
R"(        global float4 * src0,
)"
R"(        ulong  offset0,
)"
R"(        global float4 * src1,
)"
R"(        ulong  offset1,
)"
R"(        global float4 * dst,
)"
R"(        ulong  offsetd,
)"
R"(        int ne
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float4*)((global char*)src1 + offset1);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    // This performs better than using %.
)"
R"(    uint gid = get_global_id(0);
)"
R"(    uint idx1 = gid - (gid/ne)*ne; // get_global_id(0) % ne
)"
R"(    dst[gid] = src0[gid] + src1[idx1];
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_mul(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global char * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        int ne13,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(    src0 = src0 + offset0;
)"
R"(    src1 = src1 + offset1;
)"
R"(    dst  = dst + offsetd;
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int i13 = i03 % ne13;
)"
R"(    int i12 = i02 % ne12;
)"
R"(    int i11 = i01 % ne11;
)"
R"(
)"
R"(    global char * src0_ptr = src0 + i03*nb03 + i02*nb02 + i01*nb01;
)"
R"(    global char * src1_ptr = src1 + i13*nb13 + i12*nb12 + i11*nb11;
)"
R"(    global char * dst_ptr  = dst  + i03*nb3  + i02*nb2  + i01*nb1;
)"
R"(
)"
R"(    for (int i0 = get_local_id(0); i0 < ne0; i0 += get_local_size(0)) {
)"
R"(        const int i10 = i0 % ne10;
)"
R"(        *((global float *)(dst_ptr + i0*nb0)) = *((global float *)(src0_ptr + i0*nb00)) * *((global float *)(src1_ptr + i10*nb10));
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(// assumption: src1 is a row
)"
R"(// broadcast src1 into src0
)"
R"(kernel void kernel_mul_row(
)"
R"(        global float4 * src0,
)"
R"(        ulong offset0,
)"
R"(        global float4 * src1,
)"
R"(        ulong offset1,
)"
R"(        global float4 * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float4*)((global char*)src1 + offset1);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    // This performs better than using %.
)"
R"(    uint gid = get_global_id(0);
)"
R"(    uint idx1 = gid - (gid/ne)*ne; // get_global_id(0) % ne
)"
R"(    dst[gid] = src0[gid] * src1[idx1];
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// scale
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_scale(
)"
R"(        global float4 * src0,
)"
R"(        ulong offset0,
)"
R"(        global float4 * dst,
)"
R"(        ulong offsetd,
)"
R"(        float scale
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(    dst[get_global_id(0)] = src0[get_global_id(0)] * scale;
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// gelu
)"
R"(//------------------------------------------------------------------------------
)"
R"(#define GELU_COEF_A     0.044715f
)"
R"(#define SQRT_2_OVER_PI  0.79788456080286535587989211986876f
)"
R"(
)"
R"(kernel void kernel_gelu(
)"
R"(    global float * src0,
)"
R"(    ulong offset0,
)"
R"(    global float * dst,
)"
R"(    ulong offsetd
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    float x = src0[get_global_id(0)];
)"
R"(
)"
R"(    dst[get_global_id(0)] = 0.5f*x*(1.0f + tanh(SQRT_2_OVER_PI*x*(1.0f + GELU_COEF_A*x*x)));
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_gelu_4(
)"
R"(    global float4 * src0,
)"
R"(    ulong offset0,
)"
R"(    global float4 * dst,
)"
R"(    ulong offsetd
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    float4 x = src0[get_global_id(0)];
)"
R"(
)"
R"(    dst[get_global_id(0)] = 0.5f*x*(1.0f + tanh(SQRT_2_OVER_PI*x*(1.0f + GELU_COEF_A*x*x)));
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// silu
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_silu(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    float x = src0[get_global_id(0)];
)"
R"(    dst[get_global_id(0)] = x / (1.0f + exp(-x));
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_silu_4(
)"
R"(        global float4 * src0,
)"
R"(        ulong offset0,
)"
R"(        global float4 * dst,
)"
R"(        ulong offsetd
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    float4 x = src0[get_global_id(0)];
)"
R"(    dst[get_global_id(0)] = x / (1.0f + exp(-x));
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// relu
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_relu(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    dst[get_global_id(0)] = fmax(0.0f, src0[get_global_id(0)]);
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// clamp
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_clamp(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        float min,
)"
R"(        float max
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    dst[get_global_id(0)] = src0[get_global_id(0)] < min ?
)"
R"(        min :
)"
R"(        (src0[get_global_id(0)] > max ? max : src0[get_global_id(0)]);
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// norm
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_norm(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        ulong nb01,
)"
R"(        float eps,
)"
R"(        local float * sum
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    dst = (global void*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    global float * x = (global float *) ((global char *) src0 + get_group_id(0)*nb01);
)"
R"(
)"
R"(    // MEAN
)"
R"(    // parallel sum
)"
R"(    sum[get_local_id(0)] = 0.0f;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        sum[get_local_id(0)] += x[i00];
)"
R"(    }
)"
R"(    // reduce
)"
R"(    barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    for (uint i = get_local_size(0)/2; i > 0; i /= 2) {
)"
R"(        if (get_local_id(0) < i) {
)"
R"(            sum[get_local_id(0)] += sum[get_local_id(0) + i];
)"
R"(        }
)"
R"(        barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    }
)"
R"(    float mean  = sum[0] / ne00;
)"
R"(
)"
R"(    // recenter and VARIANCE
)"
R"(    barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    global float * y = dst + get_group_id(0)*ne00;
)"
R"(    sum[get_local_id(0)] = 0.0f;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        y[i00] = x[i00] - mean;
)"
R"(        sum[get_local_id(0)] += y[i00] * y[i00];
)"
R"(    }
)"
R"(
)"
R"(    // reduce
)"
R"(    barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    for (uint i = get_local_size(0)/2; i > 0; i /= 2) {
)"
R"(        if (get_local_id(0) < i) {
)"
R"(            sum[get_local_id(0)] += sum[get_local_id(0) + i];
)"
R"(        }
)"
R"(        barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    }
)"
R"(    float variance = sum[0] / ne00;
)"
R"(
)"
R"(    float scale = 1.0f/sqrt(variance + eps);
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        y[i00] = y[i00] * scale;
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// rms_norm
)"
R"(//------------------------------------------------------------------------------
)"
R"(// This kernel depends on subgroup size.
)"
R"(kernel void kernel_rms_norm(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        ulong nb01,
)"
R"(        float eps,
)"
R"(        local float * sum // Note, the size depends on number of subgroups
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    global float4 * x = (global float4 *) ((global char *) src0 + get_group_id(0)*nb01);
)"
R"(    global float * x_scalar = (global float *) x;
)"
R"(    float4 sumf = 0;
)"
R"(    float all_sum = 0;
)"
R"(
)"
R"(    // parallel sum
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00/4; i00 += get_local_size(0)) {
)"
R"(        sumf += x[i00] * x[i00];
)"
R"(    }
)"
R"(    all_sum = sumf.s0 + sumf.s1 + sumf.s2 + sumf.s3;
)"
R"(    all_sum = sub_group_reduce_add(all_sum);
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        sum[get_sub_group_id()] = all_sum;
)"
R"(    }
)"
R"(
)"
R"(    barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(    // broadcast
)"
R"(    for (uint i = get_local_size(0) / get_max_sub_group_size() / 2; i > 0; i /= 2) {
)"
R"(       if (get_local_id(0) < i) {
)"
R"(           sum[get_local_id(0)] += sum[get_local_id(0) + i];
)"
R"(       }
)"
R"(    }
)"
R"(    if (get_local_id(0) == 0) {
)"
R"(        for (int i = 4 * (ne00 / 4); i < ne00; i++) {
)"
R"(            sum[0] += x_scalar[i];
)"
R"(        }
)"
R"(        sum[0] /= ne00;
)"
R"(    }
)"
R"(
)"
R"(    barrier(CLK_LOCAL_MEM_FENCE);
)"
R"(
)"
R"(    const float mean  = sum[0];
)"
R"(    const float scale = 1.0f/sqrt(mean + eps);
)"
R"(
)"
R"(    global float4 * y = (global float4 *) (dst + get_group_id(0)*ne00);
)"
R"(    global float * y_scalar = (global float *) y;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00/4; i00 += get_local_size(0)) {
)"
R"(        y[i00] = x[i00] * scale;
)"
R"(    }
)"
R"(    if (get_local_id(0) == 0) {
)"
R"(        for (int i00 = 4 * (ne00 / 4); i00 < ne00; i00++) {
)"
R"(            y_scalar[i00] = x_scalar[i00] * scale;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// diag_mask_inf kernels
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_diag_mask_inf(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int n_past
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i02 = get_global_id(2);
)"
R"(    int i01 = get_global_id(1);
)"
R"(    int i00 = get_global_id(0);
)"
R"(
)"
R"(    if (i00 > n_past + i01) {
)"
R"(        dst[i02*ne01*ne00 + i01*ne00 + i00] = -INFINITY;
)"
R"(    } else {
)"
R"(        dst[i02*ne01*ne00 + i01*ne00 + i00] = src0[i02*ne01*ne00 + i01*ne00 + i00];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_diag_mask_inf_8(
)"
R"(        global float4 * src0,
)"
R"(        ulong offset0,
)"
R"(        global float4 * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int n_past
)"
R"() {
)"
R"(    src0 = (global float4*)((global char*)src0 + offset0);
)"
R"(    dst = (global float4*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i = 2*get_global_id(0);
)"
R"(
)"
R"(    dst[i+0] = src0[i+0];
)"
R"(    dst[i+1] = src0[i+1];
)"
R"(    int i4 = 4*i;
)"
R"(    int i02 = i4/(ne00*ne01); i4 -= i02*ne00*ne01;
)"
R"(    int i01 = i4/(ne00);      i4 -= i01*ne00;
)"
R"(    int i00 = i4;
)"
R"(    for (int k = 3; k >= 0; --k) {
)"
R"(        if (i00 + 4 + k <= n_past + i01) {
)"
R"(            break;
)"
R"(        }
)"
R"(        (&dst[i+1])[k] = -INFINITY;
)"
R"(        if (i00 + k > n_past + i01) {
)"
R"(            (&dst[i])[k] = -INFINITY;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// softmax
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_soft_max(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        float scale,
)"
R"(        float max_bias,
)"
R"(        float m0,
)"
R"(        float m1,
)"
R"(        int n_head_log2
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    global float * psrc0 = src0 + i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(    global float * pmask = src1 != src0 ? src1 + i01*ne00 : 0;
)"
R"(    global float * pdst  = dst  + i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(
)"
R"(    float slope = 1.0f;
)"
R"(
)"
R"(    // ALiBi
)"
R"(    if (max_bias > 0.0f) {
)"
R"(        int h = i02;
)"
R"(
)"
R"(        float base = h < n_head_log2 ? m0 : m1;
)"
R"(        int   exp  = h < n_head_log2 ? h + 1 : 2*(h - n_head_log2) + 1;
)"
R"(
)"
R"(        slope = pow(base, exp);
)"
R"(    }
)"
R"(
)"
R"(    // parallel max
)"
R"(    float lmax = -INFINITY;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        lmax = fmax(lmax, psrc0[i00]*scale + (pmask ? slope*pmask[i00] : 0.0f));
)"
R"(    }
)"
R"(    float max = sub_group_reduce_max(lmax);
)"
R"(
)"
R"(    // parallel sum
)"
R"(    float lsum = 0.0f;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        float exp_psrc0 = exp((psrc0[i00]*scale + (pmask ? slope*pmask[i00] : 0.0f)) - max);
)"
R"(        lsum += exp_psrc0;
)"
R"(        // Remember the result of exp here. exp is expensive, so we really do not
)"
R"(        // wish to compute it twice.
)"
R"(        pdst[i00] = exp_psrc0;
)"
R"(    }
)"
R"(
)"
R"(    const float sum = sub_group_reduce_add(lsum);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        pdst[i00] /= sum;
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef ADRENO_GPU
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_soft_max_4(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        float scale,
)"
R"(        float max_bias,
)"
R"(        float m0,
)"
R"(        float m1,
)"
R"(        int n_head_log2
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    global float4 * psrc4 = (global float4 *)(src0 + i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00);
)"
R"(    global float4 * pmask = src1 != src0 ? (global float4 *)(src1 + i01*ne00) : 0;
)"
R"(    global float4 * pdst4 = (global float4 *)(dst  + i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00);
)"
R"(
)"
R"(    float slope = 1.0f;
)"
R"(
)"
R"(    // ALiBi
)"
R"(    if (max_bias > 0.0f) {
)"
R"(        int h = i02;
)"
R"(
)"
R"(        float base = h < n_head_log2 ? m0 : m1;
)"
R"(        int   exp  = h < n_head_log2 ? h + 1 : 2*(h - n_head_log2) + 1;
)"
R"(
)"
R"(        slope = pow(base, exp);
)"
R"(    }
)"
R"(
)"
R"(    // parallel max
)"
R"(    float4 lmax4 = -INFINITY;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00/4; i00 += get_local_size(0)) {
)"
R"(        lmax4 = fmax(lmax4, psrc4[i00]*scale + (pmask ? slope*pmask[i00] : 0.0f));
)"
R"(    }
)"
R"(    float lmax = fmax(fmax(lmax4.s0, lmax4.s1), fmax(lmax4.s2, lmax4.s3));
)"
R"(
)"
R"(    const float max = sub_group_reduce_max(lmax);
)"
R"(
)"
R"(    // parallel sum
)"
R"(    float4 lsum4 = 0.0f;
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00/4; i00 += get_local_size(0)) {
)"
R"(        const float4 exp_psrc4 = exp((psrc4[i00]*scale + (pmask ? slope*pmask[i00] : 0.0f)) - max);
)"
R"(        lsum4 += exp_psrc4;
)"
R"(        pdst4[i00] = exp_psrc4;
)"
R"(    }
)"
R"(    float lsum = lsum4.s0 + lsum4.s1 + lsum4.s2 + lsum4.s3;
)"
R"(
)"
R"(    const float sum = sub_group_reduce_add(lsum);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00/4; i00 += get_local_size(0)) {
)"
R"(        pdst4[i00] /= sum;
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// kernel_rope
)"
R"(//------------------------------------------------------------------------------
)"
R"(float rope_yarn_ramp(float low, float high, int i0) {
)"
R"(    const float y = (i0 / 2 - low) / max(0.001f, high - low);
)"
R"(    return 1.0f - min(1.0f, max(0.0f, y));
)"
R"(}
)"
R"(
)"
R"(// YaRN algorithm based on LlamaYaRNScaledRotaryEmbedding.py from https://github.com/jquesnelle/yarn
)"
R"(// MIT licensed. Copyright (c) 2023 Jeffrey Quesnelle and Bowen Peng.
)"
R"(float2 rope_yarn(
)"
R"(    float theta_extrap, float freq_scale, float2 corr_dims, int i0, float ext_factor, float mscale
)"
R"() {
)"
R"(    // Get n-d rotational scaling corrected for extrapolation
)"
R"(    float theta_interp = freq_scale * theta_extrap;
)"
R"(    float theta = theta_interp;
)"
R"(    if (ext_factor != 0.0f) {
)"
R"(        float ramp_mix = rope_yarn_ramp(corr_dims.s0, corr_dims.s1, i0) * ext_factor;
)"
R"(        theta = theta_interp * (1 - ramp_mix) + theta_extrap * ramp_mix;
)"
R"(
)"
R"(        // Get n-d magnitude scaling corrected for interpolation
)"
R"(        mscale *= 1.0f + 0.1f * log(1.0f / freq_scale);
)"
R"(    }
)"
R"(    return (float2)(cos(theta) * mscale, sin(theta) * mscale);
)"
R"(}
)"
R"(
)"
R"(// Apparently solving `n_rot = 2pi * x * base^((2 * max_pos_emb) / n_dims)` for x, we get
)"
R"(// `corr_fac(n_rot) = n_dims * log(max_pos_emb / (n_rot * 2pi)) / (2 * log(base))`
)"
R"(float rope_yarn_corr_factor(int n_dims, int n_ctx_orig, float n_rot, float base) {
)"
R"(    return n_dims * log(n_ctx_orig / (n_rot * 2 * M_PI_F)) / (2 * log(base));
)"
R"(}
)"
R"(
)"
R"(float2 rope_yarn_corr_dims(
)"
R"(    int n_dims, int n_ctx_orig, float freq_base, float beta_fast, float beta_slow
)"
R"() {
)"
R"(    // start and end correction dims
)"
R"(    return (float2)(
)"
R"(        max(0.0f,         floor(rope_yarn_corr_factor(n_dims, n_ctx_orig, beta_fast, freq_base))),
)"
R"(        min(n_dims - 1.0f, ceil(rope_yarn_corr_factor(n_dims, n_ctx_orig, beta_slow, freq_base)))
)"
R"(    );
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_rope_norm_f32(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * src2,
)"
R"(        ulong offset2,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3,
)"
R"(        int n_past,
)"
R"(        int n_dims,
)"
R"(        int n_ctx_orig,
)"
R"(        float freq_base,
)"
R"(        float freq_scale,
)"
R"(        float ext_factor,
)"
R"(        float attn_factor,
)"
R"(        float beta_fast,
)"
R"(        float beta_slow
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    src2 = (global float*)((global char*)src2 + offset2);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i3 = get_group_id(2);
)"
R"(    int i2 = get_group_id(1);
)"
R"(    int i1 = get_group_id(0);
)"
R"(
)"
R"(    float2 corr_dims = rope_yarn_corr_dims(n_dims, n_ctx_orig, freq_base, beta_fast, beta_slow);
)"
R"(
)"
R"(    global int * pos = src1;
)"
R"(
)"
R"(    float theta_base = (float) pos[i2];
)"
R"(    float inv_ndims = -1.f/n_dims;
)"
R"(
)"
R"(    for (int i0 = 2*get_local_id(0); i0 < ne0; i0 += 2*get_local_size(0)) {
)"
R"(        if (i0 < n_dims) {
)"
R"(            int ic = i0/2;
)"
R"(
)"
R"(            float theta = theta_base * pow(freq_base, inv_ndims*i0);
)"
R"(
)"
R"(            float freq_factor = src2 != src0 ? src2[ic] : 1.0f;
)"
R"(
)"
R"(            float2 cos_sin_theta = rope_yarn(theta/freq_factor, freq_scale, corr_dims, i0, ext_factor, attn_factor);
)"
R"(
)"
R"(            global float * src       = (global float *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global float * dst_data  = (global float *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            float x0 = src[0];
)"
R"(            float x1 = src[1];
)"
R"(
)"
R"(            dst_data[0] = x0*cos_sin_theta.s0 - x1*cos_sin_theta.s1;
)"
R"(            dst_data[1] = x0*cos_sin_theta.s1 + x1*cos_sin_theta.s0;
)"
R"(        } else {
)"
R"(            global float * src      = (global float *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global float * dst_data = (global float *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            dst_data[0] = src[0];
)"
R"(            dst_data[1] = src[1];
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_rope_norm_f16(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * src2,
)"
R"(        ulong offset2,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3,
)"
R"(        int n_past,
)"
R"(        int n_dims,
)"
R"(        int n_ctx_orig,
)"
R"(        float freq_base,
)"
R"(        float freq_scale,
)"
R"(        float ext_factor,
)"
R"(        float attn_factor,
)"
R"(        float beta_fast,
)"
R"(        float beta_slow
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    src2 = (global float*)((global char*)src2 + offset2);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i3 = get_group_id(2);
)"
R"(    int i2 = get_group_id(1);
)"
R"(    int i1 = get_group_id(0);
)"
R"(
)"
R"(    float2 corr_dims = rope_yarn_corr_dims(n_dims, n_ctx_orig, freq_base, beta_fast, beta_slow);
)"
R"(
)"
R"(    global int * pos = src1;
)"
R"(
)"
R"(    float theta_base = (float) pos[i2];
)"
R"(    float inv_ndims = -1.f/n_dims;
)"
R"(
)"
R"(    for (int i0 = 2*get_local_id(0); i0 < ne0; i0 += 2*get_local_size(0)) {
)"
R"(        if (i0 < n_dims) {
)"
R"(            int ic = i0/2;
)"
R"(
)"
R"(            float theta = theta_base * pow(freq_base, inv_ndims*i0);
)"
R"(
)"
R"(            float freq_factor = src2 != src0 ? src2[ic] : 1.0f;
)"
R"(
)"
R"(            float2 cos_sin_theta = rope_yarn(theta/freq_factor, freq_scale, corr_dims, i0, ext_factor, attn_factor);
)"
R"(
)"
R"(            global half * src       = (global half *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global half * dst_data  = (global half *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            float x0 = src[0];
)"
R"(            float x1 = src[1];
)"
R"(
)"
R"(            dst_data[0] = x0*cos_sin_theta.s0 - x1*cos_sin_theta.s1;
)"
R"(            dst_data[1] = x0*cos_sin_theta.s1 + x1*cos_sin_theta.s0;
)"
R"(        } else {
)"
R"(            global half * src      = (global half *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global half * dst_data = (global half *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            dst_data[0] = src[0];
)"
R"(            dst_data[1] = src[1];
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_rope_neox_f32(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * src2,
)"
R"(        ulong offset2,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3,
)"
R"(        int n_past,
)"
R"(        int n_dims,
)"
R"(        int n_ctx_orig,
)"
R"(        float freq_base,
)"
R"(        float freq_scale,
)"
R"(        float ext_factor,
)"
R"(        float attn_factor,
)"
R"(        float beta_fast,
)"
R"(        float beta_slow
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    src2 = (global float*)((global char*)src2 + offset2);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i3 = get_group_id(2);
)"
R"(    int i2 = get_group_id(1);
)"
R"(    int i1 = get_group_id(0);
)"
R"(
)"
R"(    float2 corr_dims = rope_yarn_corr_dims(n_dims, n_ctx_orig, freq_base, beta_fast, beta_slow);
)"
R"(
)"
R"(    global int * pos = src1;
)"
R"(
)"
R"(    float theta_base = (float) pos[i2];
)"
R"(    float inv_ndims = -1.f/n_dims;
)"
R"(
)"
R"(    for (int i0 = 2*get_local_id(0); i0 < ne0; i0 += 2*get_local_size(0)) {
)"
R"(        if (i0 < n_dims) {
)"
R"(            int ic = i0/2;
)"
R"(
)"
R"(            const float theta = theta_base * pow(freq_base, inv_ndims*i0);
)"
R"(
)"
R"(            const float freq_factor = src2 != src0 ? src2[ic] : 1.0f;
)"
R"(
)"
R"(            float2 cos_sin_theta = rope_yarn(theta/freq_factor, freq_scale, corr_dims, i0, ext_factor, attn_factor);
)"
R"(
)"
R"(            global float * src      = (global float *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + ic*nb00);
)"
R"(            global float * dst_data = (global float *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + ic*nb0);
)"
R"(
)"
R"(            const float x0 = src[0];
)"
R"(            const float x1 = src[n_dims/2];
)"
R"(
)"
R"(            dst_data[0]        = x0*cos_sin_theta.s0 - x1*cos_sin_theta.s1;
)"
R"(            dst_data[n_dims/2] = x0*cos_sin_theta.s1 + x1*cos_sin_theta.s0;
)"
R"(        } else {
)"
R"(            global float * const src = (global float *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global float * dst_data  = (global float *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            dst_data[0] = src[0];
)"
R"(            dst_data[1] = src[1];
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_rope_neox_f16(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * src2,
)"
R"(        ulong offset2,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3,
)"
R"(        int n_past,
)"
R"(        int n_dims,
)"
R"(        int n_ctx_orig,
)"
R"(        float freq_base,
)"
R"(        float freq_scale,
)"
R"(        float ext_factor,
)"
R"(        float attn_factor,
)"
R"(        float beta_fast,
)"
R"(        float beta_slow
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    src2 = (global float*)((global char*)src2 + offset2);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i3 = get_group_id(2);
)"
R"(    int i2 = get_group_id(1);
)"
R"(    int i1 = get_group_id(0);
)"
R"(
)"
R"(    float2 corr_dims = rope_yarn_corr_dims(n_dims, n_ctx_orig, freq_base, beta_fast, beta_slow);
)"
R"(
)"
R"(    global int * pos = src1;
)"
R"(
)"
R"(    float theta_base = (float) pos[i2];
)"
R"(    float inv_ndims = -1.f/n_dims;
)"
R"(
)"
R"(    for (int i0 = 2*get_local_id(0); i0 < ne0; i0 += 2*get_local_size(0)) {
)"
R"(        if (i0 < n_dims) {
)"
R"(            int ic = i0/2;
)"
R"(
)"
R"(            const float theta = theta_base * pow(freq_base, inv_ndims*i0);
)"
R"(
)"
R"(            const float freq_factor = src2 != src0 ? src2[ic] : 1.0f;
)"
R"(
)"
R"(            float2 cos_sin_theta = rope_yarn(theta/freq_factor, freq_scale, corr_dims, i0, ext_factor, attn_factor);
)"
R"(
)"
R"(            global half * src       = (global half *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + ic*nb00);
)"
R"(            global half * dst_data  = (global half *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + ic*nb0);
)"
R"(
)"
R"(            const float x0 = src[0];
)"
R"(            const float x1 = src[n_dims/2];
)"
R"(
)"
R"(            dst_data[0]        = x0*cos_sin_theta.s0 - x1*cos_sin_theta.s1;
)"
R"(            dst_data[n_dims/2] = x0*cos_sin_theta.s1 + x1*cos_sin_theta.s0;
)"
R"(        } else {
)"
R"(            global half * const src = (global half *)((global char *) src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
)"
R"(            global half * dst_data  = (global half *)((global char *)  dst + i3*nb3  + i2*nb2  + i1*nb1  + i0*nb0);
)"
R"(
)"
R"(            dst_data[0] = src[0];
)"
R"(            dst_data[1] = src[1];
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// cpy
)"
R"(//------------------------------------------------------------------------------
)"
R"(
)"
R"(kernel void kernel_cpy_f16_f16(
)"
R"(        global half * src0,
)"
R"(        ulong offset0,
)"
R"(        global half * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(    src0 = (global half*)((global char*)src0 + offset0);
)"
R"(    dst = (global half*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int n = i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(
)"
R"(    int i3 = n / (ne2*ne1*ne0);
)"
R"(    int i2 = (n - i3*ne2*ne1*ne0) / (ne1*ne0);
)"
R"(    int i1 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0) / ne0;
)"
R"(    int i0 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0 - i1*ne0);
)"
R"(
)"
R"(    global half * dst_data = (global half *) ((global char *) dst + i3*nb3 + i2*nb2 + i1*nb1 + i0*nb0);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        global const half * src = (global half *)((global char *) src0 + i03*nb03 + i02*nb02 + i01*nb01 + i00*nb00);
)"
R"(        dst_data[i00] = src[0];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_cpy_f16_f32(
)"
R"(        global half * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(
)"
R"(    src0 = (global half*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int n = i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(
)"
R"(    int i3 = n / (ne2*ne1*ne0);
)"
R"(    int i2 = (n - i3*ne2*ne1*ne0) / (ne1*ne0);
)"
R"(    int i1 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0) / ne0;
)"
R"(    int i0 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0 - i1*ne0);
)"
R"(
)"
R"(    global float * dst_data = (global float *) ((global char *) dst + i3*nb3 + i2*nb2 + i1*nb1 + i0*nb0);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        global half * src = (global half *)((global char *) src0 + i03*nb03 + i02*nb02 + i01*nb01 + i00*nb00);
)"
R"(        dst_data[i00] = src[0];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_cpy_f32_f16(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global half * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global half*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int n = i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(
)"
R"(    int i3 = n / (ne2*ne1*ne0);
)"
R"(    int i2 = (n - i3*ne2*ne1*ne0) / (ne1*ne0);
)"
R"(    int i1 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0) / ne0;
)"
R"(    int i0 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0 - i1*ne0);
)"
R"(
)"
R"(    global half * dst_data = (global half *) ((global char *) dst + i3*nb3 + i2*nb2 + i1*nb1 + i0*nb0);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        global const float * src = (global float *)((global char *) src0 + i03*nb03 + i02*nb02 + i01*nb01 + i00*nb00);
)"
R"(
)"
R"(        dst_data[i00] = src[0];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_cpy_f32_f32(
)"
R"(        global float * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne03,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int ne2,
)"
R"(        int ne3,
)"
R"(        ulong nb0,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2,
)"
R"(        ulong nb3
)"
R"() {
)"
R"(    src0 = (global float*)((global char*)src0 + offset0);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i03 = get_group_id(2);
)"
R"(    int i02 = get_group_id(1);
)"
R"(    int i01 = get_group_id(0);
)"
R"(
)"
R"(    int n = i03*ne02*ne01*ne00 + i02*ne01*ne00 + i01*ne00;
)"
R"(
)"
R"(    int i3 = n / (ne2*ne1*ne0);
)"
R"(    int i2 = (n - i3*ne2*ne1*ne0) / (ne1*ne0);
)"
R"(    int i1 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0) / ne0;
)"
R"(    int i0 = (n - i3*ne2*ne1*ne0 - i2*ne1*ne0 - i1*ne0);
)"
R"(
)"
R"(    global float * dst_data = (global float *) ((global char *) dst + i3*nb3 + i2*nb2 + i1*nb1 + i0*nb0);
)"
R"(
)"
R"(    for (int i00 = get_local_id(0); i00 < ne00; i00 += get_local_size(0)) {
)"
R"(        global const float * src = (global float *)((global char *) src0 + i03*nb03 + i02*nb02 + i01*nb01 + i00*nb00);
)"
R"(
)"
R"(        dst_data[i00] = src[0];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// get_rows
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_get_rows_f32(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        int ne10,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i10 = get_group_id(0);
)"
R"(    int i11 = get_group_id(1);
)"
R"(
)"
R"(    int r = ((global int *) ((global char *) src1 + i11*nb11 + i10*nb10))[0];
)"
R"(
)"
R"(    int i02 = i11;
)"
R"(
)"
R"(    for (int ind = get_local_id(0); ind < ne00; ind += get_local_size(0)) {
)"
R"(        ((global float *) ((global char *) dst + i11*nb2 + i10*nb1))[ind] =
)"
R"(            ((global float *) ((global char *) src0 + r*nb01 + i02*nb02))[ind];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_get_rows_f16(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        int ne10,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int i10 = get_group_id(0);
)"
R"(    int i11 = get_group_id(1);
)"
R"(
)"
R"(    int r = ((global int32_t *) ((global char *) src1 + i11*nb11 + i10*nb10))[0];
)"
R"(
)"
R"(    int i02 = i11;
)"
R"(
)"
R"(    for (int ind = get_local_id(0); ind < ne00; ind += get_local_size(0)) {
)"
R"(        ((global float *) ((global char *) dst + i11*nb2 + i10*nb1))[ind] =
)"
R"(            ((global half *) ((global char *) src0 + r*nb01 + i02*nb02))[ind];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_get_rows_q4_0(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global int * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        int ne10,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb1,
)"
R"(        ulong nb2
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global int*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    const int NL = 2;
)"
R"(
)"
R"(    int i10 = get_group_id(0);
)"
R"(    int i11 = get_group_id(1);
)"
R"(
)"
R"(    int r = ((global int32_t *) ((global char *) src1 + i11*nb11 + i10*nb10))[0];
)"
R"(
)"
R"(    int i02 = i11;
)"
R"(
)"
R"(    for (int ind = get_local_id(0); ind < ne00/16; ind += get_local_size(0)) {
)"
R"(        float16 temp;
)"
R"(        dequantize_q4_0_f32(
)"
R"(            ((global struct block_q4_0 *) ((global char *) src0 + r*nb01 + i02*nb02)) + ind/NL, ind%NL, &temp);
)"
R"(        *(((global float16 *) ((global char *) dst + i11*nb2 + i10*nb1)) + ind) = temp;
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_mat_f32_f32
)"
R"(//------------------------------------------------------------------------------
)"
R"(#define N_F32_F32 4
)"
R"(
)"
R"(kernel void kernel_mul_mat_f32_f32(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global char*)((global char*)src0 + offset0);
)"
R"(    src1 = (global char*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int rb = get_group_id(1)*N_F32_F32;
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = r0*nb01 + (i12/r2)*nb02 + (i13/r3)*nb03;
)"
R"(
)"
R"(    global float * x = (global float *) (src0 + offset_src0);
)"
R"(
)"
R"(    if (ne00 < 128) {
)"
R"(        for (int row = 0; row < N_F32_F32; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global float * y = (global float *) (src1 + offset_src1);
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00; i += get_max_sub_group_size()) {
)"
R"(                sumf += (float) x[i] * (float) y[i];
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    } else {
)"
R"(        global float4 * x4 = (global float4 *)x;
)"
R"(        for (int row = 0; row < N_F32_F32; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global float  * y  = (global float  *) (src1 + offset_src1);
)"
R"(            global float4 * y4 = (global float4 *) y;
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00/4; i += get_max_sub_group_size()) {
)"
R"(                sumf += (float) x4[i].s0 * y4[i].s0;
)"
R"(                sumf += (float) x4[i].s1 * y4[i].s1;
)"
R"(                sumf += (float) x4[i].s2 * y4[i].s2;
)"
R"(                sumf += (float) x4[i].s3 * y4[i].s3;
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                for (int i = 4*(ne00/4); i < ne00; ++i) {
)"
R"(                    all_sum += (float) x[i] * y[i];
)"
R"(                }
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_mat_f16_f16
)"
R"(//------------------------------------------------------------------------------
)"
R"(#define N_F16_F16 4
)"
R"(
)"
R"(kernel void kernel_mul_mat_f16_f16(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3)
)"
R"({
)"
R"(    src0 = (global char*)((global char*)src0 + offset0);
)"
R"(    src1 = (global char*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int rb = get_group_id(1)*N_F16_F16;
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = r0*nb01 + (i12/r2)*nb02 + (i13/r3)*nb03;
)"
R"(
)"
R"(    global half * x = (global half *) (src0 + offset_src0);
)"
R"(
)"
R"(    if (ne00 < 128) {
)"
R"(        for (int row = 0; row < N_F16_F16; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global half * y = (global half *) (src1 + offset_src1);
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00; i += get_max_sub_group_size()) {
)"
R"(                sumf += (half) x[i] * (half) y[i];
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    } else {
)"
R"(        global half4 * x4 = (global half4 *)x;
)"
R"(        for (int row = 0; row < N_F16_F16; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global half  * y  = (global half  *) (src1 + offset_src1);
)"
R"(            global half4 * y4 = (global half4 *) y;
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00/4; i += get_max_sub_group_size()) {
)"
R"(                sumf += (half) x4[i].s0 * y4[i].s0;
)"
R"(                sumf += (half) x4[i].s1 * y4[i].s1;
)"
R"(                sumf += (half) x4[i].s2 * y4[i].s2;
)"
R"(                sumf += (half) x4[i].s3 * y4[i].s3;
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                for (int i = 4*(ne00/4); i < ne00; ++i) {
)"
R"(                    all_sum += (half) x[i] * y[i];
)"
R"(                }
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_mat_f16_f32_1row
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_mul_mat_f16_f32_1row(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global char*)((global char*)src0 + offset0);
)"
R"(    src1 = (global char*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = r0*nb01 + (i12/r2)*nb02 + (i13/r3)*nb03;
)"
R"(    ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(    global half  * x = (global half  *) (src0 + offset_src0);
)"
R"(    global float * y = (global float *) (src1 + offset_src1);
)"
R"(
)"
R"(    float sumf = 0;
)"
R"(    if (ne00 < 128) {
)"
R"(        for (int i = get_sub_group_local_id(); i < ne00; i += get_max_sub_group_size()) {
)"
R"(            sumf += (float) x[i] * (float) y[i];
)"
R"(        }
)"
R"(        float all_sum = sub_group_reduce_add(sumf);
)"
R"(        if (get_sub_group_local_id() == 0) {
)"
R"(            dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(        }
)"
R"(    } else {
)"
R"(        global half4  * x4 = (global half4  *) x;
)"
R"(        global float4 * y4 = (global float4 *) y;
)"
R"(        for (int i = get_sub_group_local_id(); i < ne00/4; i += get_max_sub_group_size()) {
)"
R"(            sumf += (float) x4[i].s0 * y4[i].s0;
)"
R"(            sumf += (float) x4[i].s1 * y4[i].s1;
)"
R"(            sumf += (float) x4[i].s2 * y4[i].s2;
)"
R"(            sumf += (float) x4[i].s3 * y4[i].s3;
)"
R"(        }
)"
R"(        float all_sum = sub_group_reduce_add(sumf);
)"
R"(        if (get_sub_group_local_id() == 0) {
)"
R"(            for (int i = 4*(ne00/4); i < ne00; ++i) {
)"
R"(                all_sum += (float) x[i] * y[i];
)"
R"(            }
)"
R"(            dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(        }
)"
R"(    }
)"
R"(
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_mat_f16_f32
)"
R"(//------------------------------------------------------------------------------
)"
R"(#define N_F16_F32 4
)"
R"(
)"
R"(#ifdef ADRENO_GPU
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_f16_f32(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global char*)((global char*)src0 + offset0);
)"
R"(    src1 = (global char*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int rb = get_group_id(1)*N_F16_F32;
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = r0*nb01 + (i12/r2)*nb02 + (i13/r3)*nb03;
)"
R"(
)"
R"(    global half * x = (global half *) (src0 + offset_src0);
)"
R"(
)"
R"(    if (ne00 < 128) {
)"
R"(        for (int row = 0; row < N_F16_F32; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global float * y = (global float *) (src1 + offset_src1);
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00; i += get_max_sub_group_size()) {
)"
R"(                sumf += convert_float(x[i]) * y[i];
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    } else {
)"
R"(        global half4 * x4 = (global half4 *)x;
)"
R"(        for (int row = 0; row < N_F16_F32; ++row) {
)"
R"(            int r1 = rb + row;
)"
R"(            if (r1 >= ne11) {
)"
R"(                break;
)"
R"(            }
)"
R"(
)"
R"(            ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(            global float  * y  = (global float  *) (src1 + offset_src1);
)"
R"(            global float4 * y4 = (global float4 *) y;
)"
R"(
)"
R"(            float sumf = 0;
)"
R"(            for (int i = get_sub_group_local_id(); i < ne00/4; i += get_max_sub_group_size()) {
)"
R"(                sumf += convert_float(x4[i].s0) * y4[i].s0;
)"
R"(                sumf += convert_float(x4[i].s1) * y4[i].s1;
)"
R"(                sumf += convert_float(x4[i].s2) * y4[i].s2;
)"
R"(                sumf += convert_float(x4[i].s3) * y4[i].s3;
)"
R"(            }
)"
R"(
)"
R"(            float all_sum = sub_group_reduce_add(sumf);
)"
R"(            if (get_sub_group_local_id() == 0) {
)"
R"(                for (int i = 4*(ne00/4); i < ne00; ++i) {
)"
R"(                    all_sum += (float) x[i] * y[i];
)"
R"(                }
)"
R"(                dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(            }
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_mat_f16_f32_l4
)"
R"(//------------------------------------------------------------------------------
)"
R"(// Assumes row size (ne00) is a multiple of 4
)"
R"(#ifdef ADRENO_GPU
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_f16_f32_l4(
)"
R"(        global char * src0,
)"
R"(        ulong offset0,
)"
R"(        global char * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        ulong nb00,
)"
R"(        ulong nb01,
)"
R"(        ulong nb02,
)"
R"(        ulong nb03,
)"
R"(        int ne10,
)"
R"(        int ne11,
)"
R"(        int ne12,
)"
R"(        ulong nb10,
)"
R"(        ulong nb11,
)"
R"(        ulong nb12,
)"
R"(        ulong nb13,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global char*)((global char*)src0 + offset0);
)"
R"(    src1 = (global char*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    int nrows = ne11;
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = r0*nb01 + (i12/r2)*nb02 + (i13/r3)*nb03;
)"
R"(
)"
R"(    global half4 * x4 = (global half4 *) (src0 + offset_src0);
)"
R"(
)"
R"(    for (int r1 = 0; r1 < nrows; ++r1) {
)"
R"(        ulong offset_src1 = r1*nb11 + (i12   )*nb12 + (i13   )*nb13;
)"
R"(
)"
R"(        global float4 * y4 = (global float4 *) (src1 + offset_src1);
)"
R"(
)"
R"(        float sumf = 0;
)"
R"(        for (int i = get_sub_group_local_id(); i < ne00/4; i += get_max_sub_group_size()) {
)"
R"(            sumf += convert_float(x4[i].s0) * y4[i].s0;
)"
R"(            sumf += convert_float(x4[i].s1) * y4[i].s1;
)"
R"(            sumf += convert_float(x4[i].s2) * y4[i].s2;
)"
R"(            sumf += convert_float(x4[i].s3) * y4[i].s3;
)"
R"(        }
)"
R"(
)"
R"(        float all_sum = sub_group_reduce_add(sumf);
)"
R"(        if (get_sub_group_local_id() == 0) {
)"
R"(            dst[im*ne1*ne0 + r1*ne0 + r0] = all_sum;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_vec_q_n_f32
)"
R"(//------------------------------------------------------------------------------
)"
R"(// function for calculate inner product between half a q4_0 block and 16 floats (yl), sumy is SUM(yl[i])
)"
R"(// il indicates where the q4 quants begin (0 or QK4_0/4)
)"
R"(// we assume that the yl's have been multiplied with the appropriate scale factor
)"
R"(// that corresponds to the missing bit shifts (1, 1/16, 1/256, 1/4096)
)"
R"(inline float block_q_4_0_dot_y(
)"
R"(        global struct block_q4_0 * qb_curr,
)"
R"(        float sumy,
)"
R"(        private float * yl,
)"
R"(        int il
)"
R"() {
)"
R"(    float d = qb_curr->d;
)"
R"(    float2 acc = 0.f;
)"
R"(    global ushort * qs = ((global ushort *)qb_curr + 1 + il/2);
)"
R"(    for (int i = 0; i < 8; i+=2) {
)"
R"(        acc.s0 += yl[i + 0] * (qs[i / 2] & 0x000F)
)"
R"(                + yl[i + 1] * (qs[i / 2] & 0x0F00);
)"
R"(        acc.s1 += yl[i + 8] * (qs[i / 2] & 0x00F0)
)"
R"(                + yl[i + 9] * (qs[i / 2] & 0xF000);
)"
R"(    }
)"
R"(    return d * (sumy * -8.f + acc.s0 + acc.s1);
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 4 // each SIMD group works on 4 rows
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 4
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(
)"
R"(inline void mul_vec_q_n_f32(
)"
R"(        global void * src0,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(
)"
R"(    const ulong nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is essenatially the linear global
)"
R"(    // id of a SIMD group in the grid.
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset0 = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(
)"
R"(    global struct block_q4_0 * x = (global struct block_q4_0 *) src0 + offset0;
)"
R"(    global float             * y = (global float             *) src1 + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float yl[16];       // src1 vector cache
)"
R"(    float sumf[N_DST]={0.f};
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix * QK4_0 + il;
)"
R"(
)"
R"(    // each thread in a SIMD group deals with half a block.
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0;
)"
R"(        for (int i = 0; i < 8; i += 2) {
)"
R"(            sumy += yb[i] + yb[i+1];
)"
R"(            yl[i+0] = yb[i+ 0];
)"
R"(            yl[i+1] = yb[i+ 1]/256.f;
)"
R"(            sumy += yb[i+16] + yb[i+17];
)"
R"(            yl[i+8] = yb[i+16]/16.f;
)"
R"(            yl[i+9] = yb[i+17]/4096.f;
)"
R"(        }
)"
R"(
)"
R"(        for (int row = 0; row < N_DST; row++) {
)"
R"(            sumf[row] += block_q_4_0_dot_y(x+ib+row*nb, sumy, yl, il);
)"
R"(        }
)"
R"(
)"
R"(        // One thread in a SIMD group (i.e., subgroup) handles a half block,
)"
R"(        // hence then entire SIMD group handles SIMDWIDTH/2 blocks.
)"
R"(        // y points to the activation matrix (of type float). Therefore for
)"
R"(        // one thread, the # of blocks y should advance is SIMDWIDTH/2 (because
)"
R"(        // SIMDWIDTH/2 blocks are processed by a SIMD group) - in terms of
)"
R"(        // floats, it is QK4_0 * (SIMDWIDTH/2), where QK4_0 is the block size.
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    // The above does not work for Adreno - it produces incorrect results for
)"
R"(    // row = 1, 2, 3 and only row = 0 gives the correct result.
)"
R"(    // If N_DST is changed, the below array must be initialized accordingly.
)"
R"(    // This also seems to perform better on Intel.
)"
R"(    float tot[N_DST] = {
)"
R"(        sub_group_reduce_add(sumf[0]), sub_group_reduce_add(sumf[1]),
)"
R"(        sub_group_reduce_add(sumf[2]), sub_group_reduce_add(sumf[3])};
)"
R"(    for (int row = 0; row < N_DST; ++row) {
)"
R"(        if (get_sub_group_local_id() == 0 && first_row + row < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + row] = tot[row];
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_vec_q_n_f32(src0, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
R"(
)"
R"(//
)"
R"(// This variant unrolls the loops and uses vector types instead of pointers.
)"
R"(// It improves performance on Adreno but not so much on Intel.
)"
R"(//
)"
R"(inline float block_q_4_0_dot_y_v(
)"
R"(        global struct block_q4_0 * qb_curr,
)"
R"(        float sumy,
)"
R"(        float16 yl,
)"
R"(        int il
)"
R"() {
)"
R"(    float d = qb_curr->d;
)"
R"(    float acc = 0.f;
)"
R"(    global ushort * qs = ((global ushort *)qb_curr + 1 + il/2);
)"
R"(
)"
R"(    acc += yl.s0 * (qs[0] & 0x000F);
)"
R"(    acc += yl.s1 * (qs[0] & 0x0F00);
)"
R"(    acc += yl.s8 * (qs[0] & 0x00F0);
)"
R"(    acc += yl.s9 * (qs[0] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s2 * (qs[1] & 0x000F);
)"
R"(    acc += yl.s3 * (qs[1] & 0x0F00);
)"
R"(    acc += yl.sa * (qs[1] & 0x00F0);
)"
R"(    acc += yl.sb * (qs[1] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s4 * (qs[2] & 0x000F);
)"
R"(    acc += yl.s5 * (qs[2] & 0x0F00);
)"
R"(    acc += yl.sc * (qs[2] & 0x00F0);
)"
R"(    acc += yl.sd * (qs[2] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s6 * (qs[3] & 0x000F);
)"
R"(    acc += yl.s7 * (qs[3] & 0x0F00);
)"
R"(    acc += yl.se * (qs[3] & 0x00F0);
)"
R"(    acc += yl.sf * (qs[3] & 0xF000);
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);
)"
R"(}
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 4 // each SIMD group works on 4 rows
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 4
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(
)"
R"(inline void mul_vec_q_n_f32_v(
)"
R"(        global void * src0,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    const ulong nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is essenatially the linear global
)"
R"(    // id of a SIMD group in the grid.
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset0 = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(
)"
R"(    global struct block_q4_0 * x = (global struct block_q4_0 *) src0 + offset0;
)"
R"(    global float             * y = (global float             *) src1 + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float16 yl;       // src1 vector cache
)"
R"(    float4 sumf = (float4)(0.f, 0.f, 0.f, 0.f);
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix * QK4_0 + il;
)"
R"(
)"
R"(    // each thread in a SIMD group deals with half a block.
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0;
)"
R"(
)"
R"(        sumy += yb[0];
)"
R"(        sumy += yb[1];
)"
R"(        sumy += yb[2];
)"
R"(        sumy += yb[3];
)"
R"(        sumy += yb[4];
)"
R"(        sumy += yb[5];
)"
R"(        sumy += yb[6];
)"
R"(        sumy += yb[7];
)"
R"(
)"
R"(        sumy += yb[16];
)"
R"(        sumy += yb[17];
)"
R"(        sumy += yb[18];
)"
R"(        sumy += yb[19];
)"
R"(        sumy += yb[20];
)"
R"(        sumy += yb[21];
)"
R"(        sumy += yb[22];
)"
R"(        sumy += yb[23];
)"
R"(
)"
R"(
)"
R"(        yl.s0 = yb[0];
)"
R"(        yl.s1 = yb[1]/256.f;
)"
R"(
)"
R"(        yl.s2 = yb[2];
)"
R"(        yl.s3 = yb[3]/256.f;
)"
R"(
)"
R"(        yl.s4 = yb[4];
)"
R"(        yl.s5 = yb[5]/256.f;
)"
R"(
)"
R"(        yl.s6 = yb[6];
)"
R"(        yl.s7 = yb[7]/256.f;
)"
R"(
)"
R"(        yl.s8 = yb[16]/16.f;
)"
R"(        yl.s9 = yb[17]/4096.f;
)"
R"(
)"
R"(        yl.sa = yb[18]/16.f;
)"
R"(        yl.sb = yb[19]/4096.f;
)"
R"(
)"
R"(        yl.sc = yb[20]/16.f;
)"
R"(        yl.sd = yb[21]/4096.f;
)"
R"(
)"
R"(        yl.se = yb[22]/16.f;
)"
R"(        yl.sf = yb[23]/4096.f;
)"
R"(
)"
R"(        sumf.s0 += block_q_4_0_dot_y_v(x+ib+0*nb, sumy, yl, il);
)"
R"(        sumf.s1 += block_q_4_0_dot_y_v(x+ib+1*nb, sumy, yl, il);
)"
R"(        sumf.s2 += block_q_4_0_dot_y_v(x+ib+2*nb, sumy, yl, il);
)"
R"(        sumf.s3 += block_q_4_0_dot_y_v(x+ib+3*nb, sumy, yl, il);
)"
R"(
)"
R"(        // One thread in a SIMD group (i.e., subgroup) handles a half block,
)"
R"(        // hence then entire SIMD group handles SIMDWIDTH/2 blocks.
)"
R"(        // y points to the activation matrix (of type float). Therefore for
)"
R"(        // one thread, the # of blocks y should advance is SIMDWIDTH/2 (because
)"
R"(        // SIMDWIDTH/2 blocks are processed by a SIMD group) - in terms of
)"
R"(        // floats, it is QK4_0 * (SIMDWIDTH/2), where QK4_0 is the block size.
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    // The above does not work for Adreno - it produces incorrect results for
)"
R"(    // row = 1, 2, 3 and only row = 0 gives the correct result.
)"
R"(    // If N_DST is changed, the below array must be initialized accordingly.
)"
R"(    // This also seems to perform better on Intel.
)"
R"(    float4 tot = (float4)(
)"
R"(        sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1),
)"
R"(        sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3)
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_v(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_vec_q_n_f32_v(src0, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// kernel_convert_block_q4_0
)"
R"(// Convert the block_q4_0 format to 2 separate arrays (AOS -> SOA).
)"
R"(// This kernel does not deshuffle the bits.
)"
R"(//------------------------------------------------------------------------------
)"
R"(kernel void kernel_convert_block_q4_0(
)"
R"(    global struct block_q4_0 * src0,
)"
R"(    global uchar * dst_q,
)"
R"(    global half  * dst_d
)"
R"() {
)"
R"(    global struct block_q4_0 * b = (global struct block_q4_0 *) src0 + get_global_id(0);
)"
R"(    global uchar * q = (global uchar *) dst_q + QK4_0/2*get_global_id(0);
)"
R"(    global half  * d = (global half *) dst_d + get_global_id(0);
)"
R"(
)"
R"(    *d = b->d;
)"
R"(
)"
R"(    for (int i = 0; i < QK4_0/2; ++i) {
)"
R"(        q[i] = b->qs[i];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(kernel void kernel_restore_block_q4_0(
)"
R"(    global uchar * src_q,
)"
R"(    global half  * src_d,
)"
R"(    global struct block_q4_0 * dst
)"
R"() {
)"
R"(    global struct block_q4_0 * b = (global struct block_q4_0 *) dst + get_global_id(0);
)"
R"(    global uchar * q = (global uchar *) src_q + QK4_0/2*get_global_id(0);
)"
R"(    global half  * d = (global half *) src_d + get_global_id(0);
)"
R"(
)"
R"(    b->d = *d;
)"
R"(    for (int i = 0; i < QK4_0/2; ++i) {
)"
R"(        b->qs[i] = q[i];
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// mul_vec_q_n_f32_flat
)"
R"(//
)"
R"(// This variation uses flat arrays (struct of arrays, SOA) representation for
)"
R"(// quant tensors.
)"
R"(//------------------------------------------------------------------------------
)"
R"(
)"
R"(// This function requires the original shuffled weights.
)"
R"(// As a reminder, the original weights are shuffled so that (q[0], q[16]) are
)"
R"(// packed together in a byte, so are (q[1], q[17]) and so on.
)"
R"(inline float block_q_4_0_dot_y_flat(
)"
R"(        global uchar * x,
)"
R"(        global half  * dh,
)"
R"(        float sumy,
)"
R"(        float16 yl,
)"
R"(        int il
)"
R"() {
)"
R"(    float           d   = *dh;
)"
R"(    global ushort * qs  = ((global ushort *)x + il/2);
)"
R"(    float           acc = 0.f;
)"
R"(
)"
R"(    acc += yl.s0 * (qs[0] & 0x000F);
)"
R"(    acc += yl.s1 * (qs[0] & 0x0F00);
)"
R"(    acc += yl.s8 * (qs[0] & 0x00F0);
)"
R"(    acc += yl.s9 * (qs[0] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s2 * (qs[1] & 0x000F);
)"
R"(    acc += yl.s3 * (qs[1] & 0x0F00);
)"
R"(    acc += yl.sa * (qs[1] & 0x00F0);
)"
R"(    acc += yl.sb * (qs[1] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s4 * (qs[2] & 0x000F);
)"
R"(    acc += yl.s5 * (qs[2] & 0x0F00);
)"
R"(    acc += yl.sc * (qs[2] & 0x00F0);
)"
R"(    acc += yl.sd * (qs[2] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s6 * (qs[3] & 0x000F);
)"
R"(    acc += yl.s7 * (qs[3] & 0x0F00);
)"
R"(    acc += yl.se * (qs[3] & 0x00F0);
)"
R"(    acc += yl.sf * (qs[3] & 0xF000);
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);
)"
R"(}
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 4 // each SIMD group works on 4 rows
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 32
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 4
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(
)"
R"(inline void mul_vec_q_n_f32_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    const ulong nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is the linear global id of
)"
R"(    // a SIMD group in the grid. Each SIMD group produces N_DST values in the
)"
R"(    // result, hence uses nb blocks, i.e., the offset becomes first_row*nb.
)"
R"(    // Currently with llama2 7B, im is always 0.
)"
R"(    // TODO: how to handle im/gqa*(nb*ne0)?
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = (first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02)) * QK4_0/2;
)"
R"(
)"
R"(    global uchar * x = (global uchar *) src0_q + offset0_q;
)"
R"(    global half  * d = (global half  *) src0_d + offset0_d;
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float16 yl;
)"
R"(    float4 sumf = (float4)(0.f, 0.f, 0.f, 0.f);
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + il;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0.f;
)"
R"(
)"
R"(        sumy += yb[0];
)"
R"(        sumy += yb[1];
)"
R"(        sumy += yb[2];
)"
R"(        sumy += yb[3];
)"
R"(        sumy += yb[4];
)"
R"(        sumy += yb[5];
)"
R"(        sumy += yb[6];
)"
R"(        sumy += yb[7];
)"
R"(
)"
R"(        sumy += yb[16];
)"
R"(        sumy += yb[17];
)"
R"(        sumy += yb[18];
)"
R"(        sumy += yb[19];
)"
R"(        sumy += yb[20];
)"
R"(        sumy += yb[21];
)"
R"(        sumy += yb[22];
)"
R"(        sumy += yb[23];
)"
R"(
)"
R"(        yl.s0 = yb[0];
)"
R"(        yl.s1 = yb[1]/256.f;
)"
R"(
)"
R"(        yl.s2 = yb[2];
)"
R"(        yl.s3 = yb[3]/256.f;
)"
R"(
)"
R"(        yl.s4 = yb[4];
)"
R"(        yl.s5 = yb[5]/256.f;
)"
R"(
)"
R"(        yl.s6 = yb[6];
)"
R"(        yl.s7 = yb[7]/256.f;
)"
R"(
)"
R"(        yl.s8 = yb[16]/16.f;
)"
R"(        yl.s9 = yb[17]/4096.f;
)"
R"(
)"
R"(        yl.sa = yb[18]/16.f;
)"
R"(        yl.sb = yb[19]/4096.f;
)"
R"(
)"
R"(        yl.sc = yb[20]/16.f;
)"
R"(        yl.sd = yb[21]/4096.f;
)"
R"(
)"
R"(        yl.se = yb[22]/16.f;
)"
R"(        yl.sf = yb[23]/4096.f;
)"
R"(
)"
R"(        sumf.s0 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 0*nb*QK4_0/2, d + ib + 0*nb, sumy, yl, il);
)"
R"(        sumf.s1 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 1*nb*QK4_0/2, d + ib + 1*nb, sumy, yl, il);
)"
R"(        sumf.s2 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 2*nb*QK4_0/2, d + ib + 2*nb, sumy, yl, il);
)"
R"(        sumf.s3 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 3*nb*QK4_0/2, d + ib + 3*nb, sumy, yl, il);
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    float4 tot = (float4)(
)"
R"(        sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1),
)"
R"(        sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3)
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_vec_q_n_f32_flat(src0_q, src0_d, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
R"(
)"
R"(//
)"
R"(// This variant outputs 8 values.
)"
R"(//
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 8 // each SIMD group works on 8 rows
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 32
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 8
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(
)"
R"(inline void mul_vec_q_n_f32_8x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    const ulong nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is the linear global id of
)"
R"(    // a SIMD group in the grid. Each SIMD group produces N_DST values in the
)"
R"(    // result, hence uses nb blocks, i.e., the offset becomes first_row*nb.
)"
R"(    // Currently with llama2 7B, im is always 0.
)"
R"(    // TODO: how to handle im/gqa*(nb*ne0)?
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = (first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02)) * QK4_0/2;
)"
R"(
)"
R"(    global uchar * x = (global uchar *) src0_q + offset0_q;
)"
R"(    global half  * d = (global half  *) src0_d + offset0_d;
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float16 yl;
)"
R"(    float8 sumf = 0.f;
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + il;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0.f;
)"
R"(
)"
R"(        sumy += yb[0];
)"
R"(        sumy += yb[1];
)"
R"(        sumy += yb[2];
)"
R"(        sumy += yb[3];
)"
R"(        sumy += yb[4];
)"
R"(        sumy += yb[5];
)"
R"(        sumy += yb[6];
)"
R"(        sumy += yb[7];
)"
R"(
)"
R"(        sumy += yb[16];
)"
R"(        sumy += yb[17];
)"
R"(        sumy += yb[18];
)"
R"(        sumy += yb[19];
)"
R"(        sumy += yb[20];
)"
R"(        sumy += yb[21];
)"
R"(        sumy += yb[22];
)"
R"(        sumy += yb[23];
)"
R"(
)"
R"(        yl.s0 = yb[0];
)"
R"(        yl.s1 = yb[1]/256.f;
)"
R"(
)"
R"(        yl.s2 = yb[2];
)"
R"(        yl.s3 = yb[3]/256.f;
)"
R"(
)"
R"(        yl.s4 = yb[4];
)"
R"(        yl.s5 = yb[5]/256.f;
)"
R"(
)"
R"(        yl.s6 = yb[6];
)"
R"(        yl.s7 = yb[7]/256.f;
)"
R"(
)"
R"(        yl.s8 = yb[16]/16.f;
)"
R"(        yl.s9 = yb[17]/4096.f;
)"
R"(
)"
R"(        yl.sa = yb[18]/16.f;
)"
R"(        yl.sb = yb[19]/4096.f;
)"
R"(
)"
R"(        yl.sc = yb[20]/16.f;
)"
R"(        yl.sd = yb[21]/4096.f;
)"
R"(
)"
R"(        yl.se = yb[22]/16.f;
)"
R"(        yl.sf = yb[23]/4096.f;
)"
R"(
)"
R"(        sumf.s0 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 0*nb*QK4_0/2, d + ib + 0*nb, sumy, yl, il);
)"
R"(        sumf.s1 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 1*nb*QK4_0/2, d + ib + 1*nb, sumy, yl, il);
)"
R"(        sumf.s2 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 2*nb*QK4_0/2, d + ib + 2*nb, sumy, yl, il);
)"
R"(        sumf.s3 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 3*nb*QK4_0/2, d + ib + 3*nb, sumy, yl, il);
)"
R"(
)"
R"(        sumf.s4 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 4*nb*QK4_0/2, d + ib + 4*nb, sumy, yl, il);
)"
R"(        sumf.s5 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 5*nb*QK4_0/2, d + ib + 5*nb, sumy, yl, il);
)"
R"(        sumf.s6 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 6*nb*QK4_0/2, d + ib + 6*nb, sumy, yl, il);
)"
R"(        sumf.s7 += block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 7*nb*QK4_0/2, d + ib + 7*nb, sumy, yl, il);
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    float8 tot = (float8)(
)"
R"(        sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1),
)"
R"(        sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3),
)"
R"(        sub_group_reduce_add(sumf.s4), sub_group_reduce_add(sumf.s5),
)"
R"(        sub_group_reduce_add(sumf.s6), sub_group_reduce_add(sumf.s7)
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(
)"
R"(        if (first_row + 4 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 4] = tot.s4;
)"
R"(        }
)"
R"(        if (first_row + 5 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 5] = tot.s5;
)"
R"(        }
)"
R"(        if (first_row + 6 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 6] = tot.s6;
)"
R"(        }
)"
R"(        if (first_row + 7 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 7] = tot.s7;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_8x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_vec_q_n_f32_8x_flat(src0_q, src0_d, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
