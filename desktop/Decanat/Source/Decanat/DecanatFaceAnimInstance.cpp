#include "DecanatFaceAnimInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/Actor.h"

void UDecanatFaceAnimInstance::NativeInitializeAnimation()
{
    Super::NativeInitializeAnimation();
    if (auto* Owner = GetOwningActor()) {
        TArray<USkeletalMeshComponent*> Meshes;
        Owner->GetComponents(Meshes);
        for (auto* Mesh : Meshes) if (Mesh->GetFName() == FName(TEXT("Body"))) BodySource = Mesh;
    }
    if (BodySource && GetSkelMeshComponent()) GetSkelMeshComponent()->AddTickPrerequisiteComponent(BodySource);
}

void UDecanatFaceAnimInstance::NativeUpdateAnimation(float DeltaSeconds)
{
    Super::NativeUpdateAnimation(DeltaSeconds);
    Clock += FMath::Max(DeltaSeconds, 0.0f);
    if (Clock >= NextBlink) {
        BlinkStarted = Clock;
        NextBlink = Clock + FMath::FRandRange(3.2f, 6.0f);
    }
    const float Phase = (Clock - BlinkStarted) / 0.19f;
    IdleBlink = BlinkStarted >= 0 && Phase >= 0 && Phase <= 1
        ? FMath::Sin(PI * Phase) : 0.0f;
}
