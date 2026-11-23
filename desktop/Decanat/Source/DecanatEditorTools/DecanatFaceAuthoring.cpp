#include "DecanatMetaHumanTools.h"
#include "Engine/Blueprint.h"
#include "Engine/SimpleConstructionScript.h"
#include "Engine/SCS_Node.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimInstance.h"
#include "Animation/PoseAsset.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "../Decanat/DecanatFaceAnimInstance.h"
#include "Animation/AnimBlueprint.h"
#include "Factories/AnimBlueprintFactory.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_Root.h"
#include "AnimGraphNode_CopyPoseFromMesh.h"
#include "AnimGraphNode_ModifyCurve.h"
#include "AnimGraphNode_PoseBlendNode.h"
#include "K2Node_VariableGet.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "UObject/UnrealType.h"
#include "Misc/PackageName.h"
#if DECANAT_WITH_ACE
#include "ACEAudioCurveSourceComponent.h"
#include "AnimNode_ApplyACEAnimation.h"
#endif

FString UDecanatMetaHumanTools::InspectBlueprint(UObject* Asset)
{
    auto Report = MakeShared<FJsonObject>();
    auto* Blueprint = Cast<UBlueprint>(Asset);
    if (!Blueprint) return TEXT("{\"issue\":\"not_a_blueprint\"}");
    Report->SetStringField(TEXT("asset"), Blueprint->GetPathName());
    Report->SetBoolField(TEXT("compile_error"), Blueprint->Status == BS_Error);
    TArray<TSharedPtr<FJsonValue>> Components;
    if (Blueprint->SimpleConstructionScript) {
        for (auto* Node : Blueprint->SimpleConstructionScript->GetAllNodes()) {
            auto Item = MakeShared<FJsonObject>();
            Item->SetStringField(TEXT("name"), Node->GetVariableName().ToString());
            Item->SetStringField(TEXT("class"), Node->ComponentTemplate->GetClass()->GetPathName());
            if (auto* Mesh = Cast<USkeletalMeshComponent>(Node->ComponentTemplate)) {
                Item->SetStringField(TEXT("animation_class"), Mesh->GetAnimClass() ? Mesh->GetAnimClass()->GetPathName() : TEXT(""));
                auto* SkeletalMesh = Mesh->GetSkeletalMeshAsset();
                Item->SetStringField(TEXT("mesh"), SkeletalMesh ? SkeletalMesh->GetPathName() : TEXT(""));
                UClass* Post = SkeletalMesh ? SkeletalMesh->GetPostProcessAnimBlueprint().Get() : nullptr;
                Item->SetStringField(TEXT("post_process_class"), Post ? Post->GetPathName() : TEXT(""));
            }
            Components.Add(MakeShared<FJsonValueObject>(Item));
        }
    }
    Report->SetArrayField(TEXT("components"), Components);
    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);
    TArray<TSharedPtr<FJsonValue>> GraphItems;
    for (auto* Graph : Graphs) {
        auto GraphItem = MakeShared<FJsonObject>();
        GraphItem->SetStringField(TEXT("name"), Graph->GetName());
        TArray<TSharedPtr<FJsonValue>> Nodes;
        for (UEdGraphNode* Node : Graph->Nodes) {
            auto Item = MakeShared<FJsonObject>();
            Item->SetStringField(TEXT("name"), Node->GetName());
            Item->SetStringField(TEXT("class"), Node->GetClass()->GetPathName());
            Item->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
            TArray<TSharedPtr<FJsonValue>> Pins;
            for (auto* Pin : Node->Pins) {
                auto PinItem = MakeShared<FJsonObject>();
                PinItem->SetStringField(TEXT("name"), Pin->PinName.ToString());
                PinItem->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
                PinItem->SetBoolField(TEXT("output"), Pin->Direction == EGPD_Output);
                PinItem->SetStringField(TEXT("default"), Pin->DefaultValue);
                TArray<TSharedPtr<FJsonValue>> Links;
                for (auto* Link : Pin->LinkedTo)
                    Links.Add(MakeShared<FJsonValueString>(Link->GetOwningNode()->GetName() + TEXT(".") + Link->PinName.ToString()));
                PinItem->SetArrayField(TEXT("links"), Links);
                Pins.Add(MakeShared<FJsonValueObject>(PinItem));
            }
            Item->SetArrayField(TEXT("pins"), Pins);
            Nodes.Add(MakeShared<FJsonValueObject>(Item));
        }
        GraphItem->SetArrayField(TEXT("nodes"), Nodes);
        GraphItems.Add(MakeShared<FJsonValueObject>(GraphItem));
    }
    Report->SetArrayField(TEXT("graphs"), GraphItems);
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

FString UDecanatMetaHumanTools::InspectPoseAsset(UObject* Asset)
{
    auto* Pose = Cast<UPoseAsset>(Asset);
    if (!Pose) return TEXT("{\"issue\":\"not_a_pose_asset\"}");
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("asset"), Pose->GetPathName());
    Report->SetStringField(TEXT("skeleton"), Pose->GetSkeleton() ? Pose->GetSkeleton()->GetPathName() : TEXT(""));
    Report->SetBoolField(TEXT("additive"), Pose->IsValidAdditive());
    const TArray<FName>& Names = Pose->GetPoseFNames();
    TArray<TSharedPtr<FJsonValue>> Poses;
    for (const FName Name : Names) Poses.Add(MakeShared<FJsonValueString>(Name.ToString()));
    Report->SetArrayField(TEXT("poses"), Poses);
    TArray<TSharedPtr<FJsonValue>> Curves;
    for (const FName Name : Pose->GetCurveFNames()) Curves.Add(MakeShared<FJsonValueString>(Name.ToString()));
    Report->SetArrayField(TEXT("curves"), Curves);
    FString Result;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

UObject* UDecanatMetaHumanTools::CreateFaceAnimation(UObject* MeshAsset, UObject* MappingAsset)
{
#if DECANAT_WITH_ACE
    auto* Mesh = Cast<USkeletalMesh>(MeshAsset);
    auto* SourceMapping = Cast<UPoseAsset>(MappingAsset);
    UClass* ACEClass = LoadClass<UAnimGraphNode_Base>(nullptr, TEXT("/Script/ACEGraphNode.AnimGraphNode_ApplyACEAnimation"));
    const FString Path(TEXT("/Game/Decanat/Characters/ABP_DecanatFace"));
    const FString MappingPath(TEXT("/Game/Decanat/Animations/Face/PA_DecanatArkit"));
    if (!Mesh || !Mesh->GetSkeleton() || !SourceMapping || !ACEClass ||
        FPackageName::DoesPackageExist(Path) || FPackageName::DoesPackageExist(MappingPath)) return nullptr;
    auto* Mapping = DuplicateObject<UPoseAsset>(SourceMapping, CreatePackage(*MappingPath), TEXT("PA_DecanatArkit"));
    Mapping->SetFlags(RF_Public | RF_Standalone);
    // NVIDIA's mapping references the pre-5.6 /Game/MetaHumans skeleton, absent
    // in Creator assemblies. Bind only our copy to the actual face skeleton.
    Mapping->SetSkeleton(Mesh->GetSkeleton());
    if (auto* Source = FindFProperty<FObjectPropertyBase>(Mapping->GetClass(), TEXT("SourceAnimation")))
        Source->SetObjectPropertyValue_InContainer(Mapping, nullptr);
    Mapping->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(Mapping);
    auto* Factory = NewObject<UAnimBlueprintFactory>();
    Factory->ParentClass = UDecanatFaceAnimInstance::StaticClass();
    Factory->TargetSkeleton = Mesh->GetSkeleton();
    Factory->PreviewSkeletalMesh = Mesh;
    auto* Blueprint = Cast<UAnimBlueprint>(static_cast<UFactory*>(Factory)->FactoryCreateNew(
        UAnimBlueprint::StaticClass(), CreatePackage(*Path), TEXT("ABP_DecanatFace"),
        RF_Public | RF_Standalone, nullptr, GWarn));
    if (!Blueprint) return nullptr;
    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);
    UEdGraph* Graph = nullptr;
    UAnimGraphNode_Root* Root = nullptr;
    for (auto* Candidate : Graphs) for (UEdGraphNode* Node : Candidate->Nodes)
        if (auto* CandidateRoot = Cast<UAnimGraphNode_Root>(Node)) { Graph = Candidate; Root = CandidateRoot; }
    if (!Graph || !Root) return nullptr;
    auto Add = [Graph](UEdGraphNode* Node, int32 X) {
        Graph->AddNode(Node, false, false);
        Node->CreateNewGuid();
        Node->NodePosX = X; Node->NodePosY = 0;
        Node->PostPlacedNewNode();
        Node->AllocateDefaultPins();
    };
    auto* Copy = NewObject<UAnimGraphNode_CopyPoseFromMesh>(Graph);
    auto* CopyData = FindFProperty<FStructProperty>(Copy->GetClass(), TEXT("Node"))->ContainerPtrToValuePtr<FAnimNode_CopyPoseFromMesh>(Copy);
    CopyData->bUseAttachedParent = true;
    CopyData->bCopyCurves = false;
    Add(Copy, -1000);
    auto* Blink = NewObject<UAnimGraphNode_ModifyCurve>(Graph);
    auto* BlinkData = FindFProperty<FStructProperty>(Blink->GetClass(), TEXT("Node"))->ContainerPtrToValuePtr<FAnimNode_ModifyCurve>(Blink);
    BlinkData->AddCurve(TEXT("EyeBlinkLeft"), 0);
    BlinkData->AddCurve(TEXT("EyeBlinkRight"), 0);
    BlinkData->ApplyMode = EModifyCurveApplyMode::Blend;
    BlinkData->Alpha = 1;
    Add(Blink, -750);
    auto* ACE = NewObject<UAnimGraphNode_Base>(Graph, ACEClass);
    auto* ACEData = FindFProperty<FStructProperty>(ACE->GetClass(), TEXT("Node"))->ContainerPtrToValuePtr<FAnimNode_ApplyACEAnimation>(ACE);
    ACEData->bBlendOutToZero = true;
    ACEData->BlendOutRate = 12;
    Add(ACE, -500);
    auto* Pose = NewObject<UAnimGraphNode_PoseBlendNode>(Graph);
    auto* PoseData = FindFProperty<FStructProperty>(Pose->GetClass(), TEXT("Node"))->ContainerPtrToValuePtr<FAnimNode_PoseBlendNode>(Pose);
    PoseData->PoseAsset = Mapping;
    Add(Pose, -250);
    auto* Value = NewObject<UK2Node_VariableGet>(Graph);
    Value->VariableReference.SetSelfMember(TEXT("IdleBlink"));
    Add(Value, -1000); Value->NodePosY = 300;
    auto Connect = [Graph](UEdGraphNode* A, const TCHAR* AName, UEdGraphNode* B, const TCHAR* BName) {
        auto* Out = A->FindPin(AName, EGPD_Output);
        auto* In = B->FindPin(BName, EGPD_Input);
        return Out && In && Graph->GetSchema()->TryCreateConnection(Out, In);
    };
    const bool bLinked = Connect(Copy, TEXT("Pose"), Blink, TEXT("SourcePose")) &&
        Connect(Blink, TEXT("Pose"), ACE, TEXT("Source")) &&
        Connect(ACE, TEXT("Pose"), Pose, TEXT("SourcePose")) &&
        Connect(Pose, TEXT("Pose"), Root, TEXT("Result")) &&
        Connect(Value, TEXT("IdleBlink"), Blink, TEXT("CurveValues_0")) &&
        Connect(Value, TEXT("IdleBlink"), Blink, TEXT("CurveValues_1"));
    if (!bLinked) {
        UE_LOG(LogTemp, Error, TEXT("Face graph connection failed; inspect actual node pins before saving"));
        Blueprint->Status = BS_Error;
        return Blueprint; // Inspection allowed, caller must reject a graph with compilation errors.
    }
    if (!ConfigureFaceSource(Blueprint)) { Blueprint->Status = BS_Error; return Blueprint; }
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    Blueprint->MarkPackageDirty();
    FAssetRegistryModule::AssetCreated(Blueprint);
    return Blueprint;
#else
    return nullptr;
#endif
}

bool UDecanatMetaHumanTools::InstallFaceAnimation(UObject* CharacterAsset, UObject* AnimationAsset)
{
#if DECANAT_WITH_ACE
    auto* Blueprint = Cast<UBlueprint>(CharacterAsset);
    auto* Animation = Cast<UAnimBlueprint>(AnimationAsset);
    if (!Blueprint || !Animation || Animation->Status == BS_Error || !Animation->GeneratedClass ||
        !Blueprint->GetPathName().StartsWith(TEXT("/Game/Decanat/Characters/"))) return false;
    USCS_Node* Face = nullptr;
    for (auto* Node : Blueprint->SimpleConstructionScript->GetAllNodes())
        if (Node->GetVariableName() == FName(TEXT("Face"))) Face = Node;
    if (!Face) return false;
    auto* FaceMesh = Cast<USkeletalMeshComponent>(Face->ComponentTemplate);
    if (!FaceMesh) return false;
    FaceMesh->SetAnimInstanceClass(Animation->GeneratedClass);
    for (auto* Node : Blueprint->SimpleConstructionScript->GetAllNodes())
        if (Node->ComponentTemplate->IsA<UACEAudioCurveSourceComponent>()) return false;
    auto* Consumer = Blueprint->SimpleConstructionScript->CreateNode(UACEAudioCurveSourceComponent::StaticClass(), TEXT("DecanatFaceConsumer"));
    auto* Template = CastChecked<UACEAudioCurveSourceComponent>(Consumer->ComponentTemplate);
    Template->Volume = 0.0f; // The backend remains the only audible PCM owner.
    Template->BufferLengthInSeconds = 0.1f;
    Face->AddChildNode(Consumer);
    FKismetEditorUtilities::CompileBlueprint(Blueprint);
    Blueprint->MarkPackageDirty();
    return Blueprint->Status != BS_Error;
#else
    return false;
#endif
}

bool UDecanatMetaHumanTools::ConfigureFaceSource(UObject* Asset)
{
    auto* Blueprint = Cast<UAnimBlueprint>(Asset);
    if (!Blueprint || !Blueprint->GetPathName().StartsWith(TEXT("/Game/Decanat/"))) return false;
    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);
    for (auto* Graph : Graphs) {
        for (UEdGraphNode* Node : Graph->Nodes) {
            Node->PostPlacedNewNode();
#if DECANAT_WITH_ACE
            if (auto* Property = FindFProperty<FStructProperty>(Node->GetClass(), TEXT("Node"))) {
                if (Property->Struct == FAnimNode_ApplyACEAnimation::StaticStruct()) {
                    auto* Data = Property->ContainerPtrToValuePtr<FAnimNode_ApplyACEAnimation>(Node);
                    Data->bBlendOutToZero = true;
                    Data->BlendOutRate = 12;
                }
            }
#endif
        }
        for (UEdGraphNode* Node : Graph->Nodes) {
            auto* Copy = Cast<UAnimGraphNode_CopyPoseFromMesh>(Node);
            if (!Copy) continue;
            auto* Data = FindFProperty<FStructProperty>(Copy->GetClass(), TEXT("Node"))->ContainerPtrToValuePtr<FAnimNode_CopyPoseFromMesh>(Copy);
            Data->bUseAttachedParent = false;
            auto* In = Copy->FindPin(TEXT("SourceMeshComponent"), EGPD_Input);
            if (!In) return false;
            if (!In->LinkedTo.IsEmpty()) {
                if (In->LinkedTo.Num() != 1 || In->LinkedTo[0]->PinName != FName(TEXT("BodySource"))) return false;
                FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
                FKismetEditorUtilities::CompileBlueprint(Blueprint);
                Blueprint->MarkPackageDirty();
                return Blueprint->Status != BS_Error;
            }
            auto* Value = NewObject<UK2Node_VariableGet>(Graph);
            Value->VariableReference.SetSelfMember(TEXT("BodySource"));
            Graph->AddNode(Value, false, false);
            Value->CreateNewGuid(); Value->NodePosX = Copy->NodePosX - 250;
            Value->PostPlacedNewNode();
            Value->AllocateDefaultPins();
            auto* Out = Value->FindPin(TEXT("BodySource"), EGPD_Output);
            if (!Out || !In || !In->LinkedTo.IsEmpty() || !Graph->GetSchema()->TryCreateConnection(Out, In)) return false;
            FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
            FKismetEditorUtilities::CompileBlueprint(Blueprint);
            Blueprint->MarkPackageDirty();
            return Blueprint->Status != BS_Error;
        }
    }
    return false;
}
